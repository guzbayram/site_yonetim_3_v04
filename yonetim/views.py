# yonetim/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from .models import Site, Kullanici, Blok, Daire, Aidat, Gider
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
import re
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.utils import timezone
from datetime import date
import logging
import uuid
from django.urls import reverse, reverse_lazy
from .forms import AidatForm, GiderForm

logger = logging.getLogger(__name__)

# --- HELPER FONKSİYONLAR ---
def daire_natural_sort_key(daire_obj):
    return (daire_obj.blok.ad.lower(), daire_obj.get_sortable_daire_no())

def get_user_daire(user, site_kodu_user):
    if user.is_authenticated:
        try:
            return Daire.objects.filter(kullanici=user, blok__site__kod=site_kodu_user).select_related('blok', 'blok__site').first()
        except Daire.DoesNotExist:
            return None
    return None

def generate_unique_username(first_name, last_name, site_kodu):
    char_map = {
        'ı': 'i', 'İ': 'I', 'ö': 'o', 'Ö': 'O', 'ü': 'u', 'Ü': 'U',
        'ş': 's', 'Ş': 'S', 'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G'
    }
    clean_first_name = first_name.lower()
    clean_last_name = last_name.lower()
    for tr_char, en_char in char_map.items():
        clean_first_name = clean_first_name.replace(tr_char, en_char)
        clean_last_name = clean_last_name.replace(tr_char, en_char)

    clean_first_name = re.sub(r'\W+', '', clean_first_name)
    clean_last_name = re.sub(r'\W+', '', clean_last_name)
    base_username = f"{clean_first_name}{clean_last_name}_{site_kodu.lower()}"
    max_len_for_base = 150 - (6 + 1)
    username_candidate = base_username[:max_len_for_base]
    final_username_try = f"{username_candidate}_{uuid.uuid4().hex[:6]}"
    counter = 1
    original_final_username_base = username_candidate
    while Kullanici.objects.filter(username=final_username_try).exists():
        new_uuid_part = uuid.uuid4().hex[:6]
        final_username_try = f"{original_final_username_base[:150-(len(new_uuid_part)+1)]}_{new_uuid_part}"
        counter += 1
        if counter > 100: raise Exception("Benzersiz kullanıcı adı üretilemedi (çok fazla çakışma).")
    return final_username_try[:150]

# --- VIEW FONKSİYONLARI ---

@login_required
def panel(request):
    try:
        site_obj = Site.objects.get(kod=request.user.site_kodu)
    except Site.DoesNotExist:
        messages.error(request, "Site bulunamadı. Lütfen site kodunuzu kontrol edin veya bir yönetici ile iletişime geçin.")
        logout(request)
        return redirect('yonetim:giris')

    kullanici_kendi_dairesi = get_user_daire(request.user, request.user.site_kodu)
    # Sakinler aidat ekleyebilir ama gider ekleyemez veya başkalarının aidat/giderlerini düzenleyemez/silemez.
    # Yönetici her şeyi yapabilir.
    can_manage_site_finances = request.user.is_yonetici 

    if request.method == 'POST':
        if 'aidat_ekle' in request.POST: # Sadece kendi dairesine aidat ekleyebilmeli
            if not kullanici_kendi_dairesi:
                messages.error(request, "Aidat eklenecek bir daireniz bulunmamaktadır.")
                return redirect('yonetim:panel')
            
            aidat_form_data = AidatForm(request.POST, request.FILES)
            if aidat_form_data.is_valid():
                aidat = aidat_form_data.save(commit=False)
                aidat.daire = kullanici_kendi_dairesi
                if not aidat.tutar and site_obj.aidat_miktari: # Formda tutar girilmediyse ve sitede standart aidat varsa onu kullan
                    aidat.tutar = site_obj.aidat_miktari
                aidat.save()
                messages.success(request, f"{kullanici_kendi_dairesi.daire_tam_adi} için aidat başarıyla eklendi.")
            else:
                for field, errors in aidat_form_data.errors.items():
                    for error in errors:
                        messages.error(request, f"Aidat eklenirken hata ({aidat_form_data.fields[field].label if field in aidat_form_data.fields else field}): {error}")
            return redirect('yonetim:panel')

        elif 'gider_ekle' in request.POST:
            if not can_manage_site_finances: # Sadece yöneticiler gider ekleyebilir
                messages.error(request, "Bu işlemi yapma yetkiniz yok.")
                return redirect('yonetim:panel')
            
            gider_form_data = GiderForm(request.POST, request.FILES)
            if gider_form_data.is_valid():
                gider = gider_form_data.save(commit=False)
                gider.site = site_obj
                gider.save()
                messages.success(request, "Gider başarıyla eklendi.")
            else:
                for field, errors in gider_form_data.errors.items():
                    for error in errors:
                        messages.error(request, f"Gider eklenirken hata ({gider_form_data.fields[field].label if field in gider_form_data.fields else field}): {error}")
            return redirect('yonetim:panel')

    # Genel Toplamlar (Tüm kullanıcılar görür)
    toplam_gelir_aidatlar = Aidat.objects.filter(daire__blok__site=site_obj).aggregate(toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    toplam_giderler = Gider.objects.filter(site=site_obj).aggregate(toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    kasa_bakiyesi = toplam_gelir_aidatlar - toplam_giderler

    # Aidat Listesi (Özet) (Tüm kullanıcılar görür)
    daire_aidat_ozetleri_listesi = []
    tum_daireler_qs = Daire.objects.filter(blok__site=site_obj).select_related('blok', 'kullanici')
    daireler_listesi_sirali = sorted(list(tum_daireler_qs), key=daire_natural_sort_key)
    current_year = timezone.now().year
    yillik_aidat_borcu_bir_daire_icin = (site_obj.aidat_miktari * 12) if site_obj.aidat_miktari else Decimal('0.00')

    for daire_obj_loop in daireler_listesi_sirali:
        start_of_year = date(current_year, 1, 1); end_of_year = date(current_year, 12, 31)
        bu_yil_odenen_tutar = Aidat.objects.filter(
            daire=daire_obj_loop, tarih__gte=start_of_year, tarih__lte=end_of_year
        ).aggregate(toplam_odenen=Sum('tutar'))['toplam_odenen'] or Decimal('0.00')
        bakiye = bu_yil_odenen_tutar - yillik_aidat_borcu_bir_daire_icin
        daire_aidat_ozetleri_listesi.append({
            'daire': daire_obj_loop,
            'blok_daire': f"{daire_obj_loop.blok.ad.upper()} - {daire_obj_loop.daire_no}",
            'sakin': daire_obj_loop.kullanici.get_full_name() if daire_obj_loop.kullanici else "Boş",
            'yillik_borc': yillik_aidat_borcu_bir_daire_icin,
            'odenen': bu_yil_odenen_tutar,
            'bakiye': bakiye,
            'is_current_user_flat': kullanici_kendi_dairesi and daire_obj_loop.id == kullanici_kendi_dairesi.id
        })

    if not request.user.is_yonetici and kullanici_kendi_dairesi: # Sakin kendi dairesini en üstte görür
        kendi_daire_ozeti = next((item for item in daire_aidat_ozetleri_listesi if item['daire'].id == kullanici_kendi_dairesi.id), None)
        if kendi_daire_ozeti:
            daire_aidat_ozetleri_listesi.remove(kendi_daire_ozeti)
            daire_aidat_ozetleri_listesi.insert(0, kendi_daire_ozeti)

    # Site Gider Listesi ve Daire Aidat Detay Listesi (Tüm kullanıcılar görür, düzenleme yetkisi şablonda kontrol edilir)
    site_gider_listesi_data = Gider.objects.filter(site=site_obj).order_by('-tarih', '-id')[:50]
    daire_aidat_detay_listesi_data = Aidat.objects.filter(daire__blok__site=site_obj).select_related('daire__blok', 'daire__kullanici').order_by('-tarih', '-id')[:50]

    # Formlar
    aidat_ekle_form_initial = {'tarih': timezone.now().date()}
    if site_obj.aidat_miktari: aidat_ekle_form_initial['tutar'] = site_obj.aidat_miktari
    aidat_ekle_form = AidatForm(initial=aidat_ekle_form_initial) if kullanici_kendi_dairesi else None # Sadece dairesi olan aidat ekleyebilir
    gider_ekle_form = GiderForm(initial={'tarih': timezone.now().date()}) if can_manage_site_finances else None # Sadece yönetici gider ekleyebilir

    context = {
        'site': site_obj,
        'kullanici_kendi_dairesi': kullanici_kendi_dairesi,
        'current_year': current_year,
        'toplam_gelir_aidatlar': toplam_gelir_aidatlar,
        'toplam_giderler': toplam_giderler,
        'kasa_bakiyesi': kasa_bakiyesi,
        'aidat_listesi_ozet': daire_aidat_ozetleri_listesi,
        'site_gider_listesi': site_gider_listesi_data,
        'daire_aidat_detay_listesi': daire_aidat_detay_listesi_data,
        'aidat_ekle_form': aidat_ekle_form,
        'gider_ekle_form': gider_ekle_form,
        'can_manage_site_finances': can_manage_site_finances, # Şablonda yetki kontrolü için
        'can_edit_specific_aidat': request.user.is_yonetici, # Genel bir bayrak, şablonda daha detaylı kontrol gerekebilir
    }
    return render(request, 'yonetim/panel.html', context)

@login_required
@transaction.atomic
def site_bilgi(request):
    # Hem yönetici hem de sakinler bu sayfayı görebilir.
    # Sakinler için salt okunur mod aktif olacak.
    is_read_only_mode = not request.user.is_yonetici
    site_obj = None
    try:
        site_obj = Site.objects.select_related('yonetici').get(kod=request.user.site_kodu)
    except Site.DoesNotExist:
        if request.user.is_yonetici: # Yönetici ise ve site yoksa, yeni site oluşturma senaryosu
            pass # site_obj None kalacak, template yeni site modunda açılacak
        else: # Sakin ise ve site yoksa, bu bir hata durumudur.
            messages.error(request, "İlişkili site bulunamadı. Lütfen yöneticinizle iletişime geçin.")
            return redirect('yonetim:panel')

    # Yeni site oluşturuluyorsa (sadece yönetici için) ve site_obj None ise
    if site_obj is None and not request.user.is_yonetici:
        # Bu durum normalde yukarıdaki Site.DoesNotExist ile yakalanmalı ama ekstra bir güvenlik.
        messages.error(request, "Site bilgileri görüntülenemiyor.")
        return redirect('yonetim:panel')
    
    # Yönetici kendi sitesini yönetmiyorsa (güvenlik)
    if site_obj and site_obj.yonetici != request.user and request.user.is_yonetici and site_obj.kod == request.user.site_kodu:
        # Site var, kodu yöneticinin koduyla aynı ama DB'deki yönetici farklı.
        # Bu veri tutarsızlığıdır. İsteğe bağlı olarak site yöneticisini güncelleyebilir veya hata verebiliriz.
        # Şimdilik, yöneticinin kendi sitesi olduğunu varsayarak devam ediyoruz veya yeni site ise zaten yönetici o olacak.
        if not site_obj.yonetici : # Eğer site var ama yöneticisi null ise ata
            site_obj.yonetici = request.user
            site_obj.save()

    yoneticinin_mevcut_dairesi = Daire.objects.filter(blok__site=site_obj, kullanici=request.user).first() if site_obj else None
    form_data_initial = {}
    if site_obj:
        form_data_initial = {'ad': site_obj.ad, 'adres': site_obj.adres, 'yonetici_tel': site_obj.yonetici_tel, 'aidat_miktari': site_obj.aidat_miktari, 'kod': site_obj.kod}
    elif request.user.is_yonetici: # Yeni site kurulumuysa, sadece site kodunu göster
        form_data_initial['kod'] = request.user.site_kodu

    if request.method == 'POST':
        if is_read_only_mode: # Sakinler POST isteği yapamaz
            messages.error(request, "Bu sayfada değişiklik yapma yetkiniz yok.")
            return redirect('yonetim:site_bilgi')

        # (POST işlemleri sadece YÖNETİCİ için devam eder)
        site_ad = request.POST.get('site_adi','').strip(); site_adresi = request.POST.get('site_adresi','').strip()
        yonetici_tel = request.POST.get('yonetici_tel','').strip(); aidat_miktari_s = request.POST.get('aidat_miktari','').strip()
        yonetici_daire_id = request.POST.get('yonetici_daire_secimi')
        
        gelen_blok_idler_str = request.POST.getlist('blok_id[]')
        gelen_blok_adlari = request.POST.getlist('blok_adi[]')
        gelen_daire_sayilari_str = request.POST.getlist('daire_sayisi[]')

        if not site_ad or not site_adresi:
            messages.error(request, "Site adı ve adres zorunlu.");
            # Hata durumunda context'i yeniden oluşturup sayfayı render etmek daha kullanıcı dostu olur.
            # Şimdilik redirect kullanıyoruz ama bu form verilerini siler.
            # Doğrusu: return render(request, 'yonetim/site_bilgi.html', error_context)
            return redirect('yonetim:site_bilgi')

        aidat_miktari_d = None
        if aidat_miktari_s:
            try: aidat_miktari_d = Decimal(aidat_miktari_s.replace(',','.'))
            except InvalidOperation: messages.error(request, "Geçersiz aidat miktarı."); return redirect('yonetim:site_bilgi')

        if site_obj is None: # Yeni site oluşturuluyor (sadece yönetici bu aşamaya gelebilir)
            site_obj = Site.objects.create(ad=site_ad, adres=site_adresi, kod=request.user.site_kodu, yonetici=request.user, yonetici_tel=yonetici_tel, aidat_miktari=aidat_miktari_d)
            messages.success(request, f"'{site_obj.ad}' sitesi oluşturuldu.")
        else:
            site_obj.ad=site_ad; site_obj.adres=site_adresi; site_obj.yonetici_tel=yonetici_tel; site_obj.aidat_miktari=aidat_miktari_d
            if not site_obj.yonetici: site_obj.yonetici = request.user
            site_obj.save(); messages.success(request, "Site bilgileri güncellendi.")

        # Yönetici daire atama
        if yonetici_daire_id and site_obj:
            if yoneticinin_mevcut_dairesi and str(yoneticinin_mevcut_dairesi.id) != yonetici_daire_id:
                yoneticinin_mevcut_dairesi.kullanici = None
                yoneticinin_mevcut_dairesi.save()
                yoneticinin_mevcut_dairesi = None # Cache'i temizle
            
            if yonetici_daire_id == "bos_birak":
                if yoneticinin_mevcut_dairesi: # Zaten None değilse
                    yoneticinin_mevcut_dairesi.kullanici=None; yoneticinin_mevcut_dairesi.save()
                messages.info(request, "Yönetici daire ataması kaldırıldı.")
            else:
                try:
                    sec_daire = Daire.objects.get(id=yonetici_daire_id, blok__site=site_obj)
                    if sec_daire.kullanici is None or sec_daire.kullanici == request.user:
                        sec_daire.kullanici = request.user; sec_daire.save()
                        messages.success(request, f"Yönetici '{sec_daire.daire_tam_adi}' dairesine atandı.")
                    else: messages.error(request, f"Seçilen daire ({sec_daire.daire_tam_adi}) başkasına ait.")
                except Daire.DoesNotExist: messages.error(request, "Yönetici için seçilen daire bulunamadı.")
        
        # Blok ve Daire işlemleri (POST içinde devamı...)
        if site_obj:
            mevcut_veritabani_blok_idler = set(str(blok.id) for blok in site_obj.bloklar.all())
            formdan_gelen_islenmis_blok_idler = set()

            for i, blok_adi_formdan in enumerate(gelen_blok_adlari):
                blok_adi_upper = blok_adi_formdan.strip().upper()
                if not blok_adi_upper: continue

                daire_sayisi_str_formdan = gelen_daire_sayilari_str[i] if i < len(gelen_daire_sayilari_str) else None
                blok_id_formdan_str = gelen_blok_idler_str[i] if i < len(gelen_blok_idler_str) and gelen_blok_idler_str[i] else None

                if daire_sayisi_str_formdan:
                    try:
                        istenen_daire_sayisi = int(daire_sayisi_str_formdan)
                        if istenen_daire_sayisi <= 0:
                            messages.warning(request, f"'{blok_adi_upper}' için daire sayısı pozitif olmalı."); continue
                        
                        blok_nesnesi = None
                        if blok_id_formdan_str:
                            try:
                                blok_nesnesi = Blok.objects.get(id=blok_id_formdan_str, site=site_obj)
                                if blok_nesnesi.ad.upper() != blok_adi_upper : # Blok adı değişmişse ve başka bir blokla çakışmıyorsa
                                    # Aynı isimde başka blok var mı kontrolü eklenebilir
                                    blok_nesnesi.ad = blok_adi_upper
                                    blok_nesnesi.save()
                            except Blok.DoesNotExist: blok_nesnesi = None
                        
                        if blok_nesnesi is None:
                            blok_nesnesi, created = Blok.objects.get_or_create(site=site_obj, ad=blok_adi_upper)
                        
                        formdan_gelen_islenmis_blok_idler.add(str(blok_nesnesi.id))
                        
                        mevcut_daireler_bu_blokta_nolar = {str(d.daire_no) for d in blok_nesnesi.daireler.all()}
                        istenen_daire_noları = {str(j) for j in range(1, istenen_daire_sayisi + 1)}

                        for daire_no_ekle in istenen_daire_noları - mevcut_daireler_bu_blokta_nolar:
                            Daire.objects.create(blok=blok_nesnesi, daire_no=daire_no_ekle)
                        
                        for daire_no_sil in mevcut_daireler_bu_blokta_nolar - istenen_daire_noları:
                            try:
                                d_sil = Daire.objects.get(blok=blok_nesnesi, daire_no=daire_no_sil)
                                if d_sil.kullanici is None: d_sil.delete()
                                else: messages.warning(request, f"{blok_nesnesi.ad} - Daire {d_sil.daire_no} dolu, silinemedi.")
                            except Daire.DoesNotExist: pass
                    except ValueError: messages.warning(request, f"'{blok_adi_upper}' için geçersiz daire sayısı: '{daire_sayisi_str_formdan}'.")
                    except Exception as e: logger.error(f"Blok/Daire: {e}", exc_info=True); messages.warning(request, f"'{blok_adi_upper}' işlenirken sorun.")
            
            silinecek_blok_idler = mevcut_veritabani_blok_idler - formdan_gelen_islenmis_blok_idler
            for blok_id_s in silinecek_blok_idler:
                try:
                    blok_s = Blok.objects.get(id=blok_id_s, site=site_obj)
                    if not blok_s.daireler.filter(kullanici__isnull=False).exists(): blok_s.delete(); messages.info(request, f"'{blok_s.ad}' bloğu silindi.")
                    else: messages.warning(request, f"'{blok_s.ad}' dolu, silinemedi.")
                except Blok.DoesNotExist: pass
        return redirect('yonetim:panel')

    # GET isteği için context
    bloklar_ve_daireleri_c = []
    sitedeki_bos_ve_yoneticiye_ait_d = [] # Yönetici kendi daire atamasını buradan seçecek
    if site_obj:
        for blok_db in site_obj.bloklar.all().order_by('ad'):
            bloklar_ve_daireleri_c.append({'ad': blok_db.ad, 'id': blok_db.id, 'daire_sayisi': blok_db.daireler.count()})
        
        # Yöneticiye atanabilecek daireler: Sitedeki tüm boş daireler + yöneticinin şu anki dairesi (eğer varsa)
        daire_q_for_yonetici_select = Daire.objects.filter(blok__site=site_obj).select_related('kullanici','blok')
        temp_d_list_for_yonetici_select = sorted(list(daire_q_for_yonetici_select), key=daire_natural_sort_key)
        for d_item in temp_d_list_for_yonetici_select:
            if d_item.kullanici is None or d_item.kullanici == request.user:
                 sitedeki_bos_ve_yoneticiye_ait_d.append(d_item)


    context = {
        'site': site_obj,
        'bloklar_ve_daireleri': bloklar_ve_daireleri_c,
        'is_yeni_site': site_obj is None and request.user.is_yonetici, # Sadece yönetici yeni site kurabilir
        'form_data': form_data_initial,
        'is_read_only': is_read_only_mode, # Şablona salt okunur modu için bayrak gönder
        'sitedeki_bos_ve_yoneticiye_ait_daireler': sitedeki_bos_ve_yoneticiye_ait_d if request.user.is_yonetici else [], # Sadece yönetici için
        'yoneticinin_mevcut_dairesi_id': yoneticinin_mevcut_dairesi.id if yoneticinin_mevcut_dairesi else None,
    }
    return render(request, 'yonetim/site_bilgi.html', context)


# Diğer view fonksiyonları (giris, kayit, cikis, ajax_*, CRUD operasyonları)
# önceki halleriyle veya küçük yetkilendirme düzeltmeleriyle kullanılabilir.
# Önemli olan `kayit` ve AJAX view'larının @login_required olmaması,
# diğer panel içi view'ların ise @login_required ve rol bazlı yetkilendirmeye sahip olmasıdır.

# (Diğer view'larınız olduğu gibi kalabilir, sadece yetkilendirme ve
# sakinlerin erişim durumları yukarıdaki panel ve site_bilgi view'larında düzenlendi.)

# Örnek olarak `giris` ve `kayit` view'larının @login_required OLMADAN halleri:
def giris(request):
    if request.user.is_authenticated:
        return redirect('yonetim:panel')
    # ... (önceki giriş mantığınız) ...
    form_data = {}
    if request.method == 'POST':
        form_data = request.POST.copy()
        first_name = form_data.get('first_name', '').strip(); last_name = form_data.get('last_name', '').strip()
        site_kodu_giris = form_data.get('site_kodu', '').strip().upper(); blok_adi_giris = form_data.get('blok_adi', '').strip().upper()
        daire_no_giris = form_data.get('daire_no', '').strip(); password_giris = form_data.get('password')
        if not all([first_name, last_name, site_kodu_giris, blok_adi_giris, daire_no_giris, password_giris]):
            messages.error(request, "Tüm alanların doldurulması zorunludur."); return render(request, 'yonetim/giris.html', {'form_data': form_data})
        user_to_auth = None
        try:
            daire_qs = Daire.objects.select_related('kullanici', 'blok__site').filter(
                blok__site__kod=site_kodu_giris, blok__ad__iexact=blok_adi_giris,
                daire_no__iexact=daire_no_giris, kullanici__is_active=True,
                kullanici__first_name__iexact=first_name, kullanici__last_name__iexact=last_name )
            matching_daire = daire_qs.first()
            if matching_daire and matching_daire.kullanici:
                user_to_auth = authenticate(request, username=matching_daire.kullanici.username, password=password_giris)
            if user_to_auth is not None: 
                if user_to_auth.site_kodu.upper() == site_kodu_giris: # Ek güvenlik, kullanıcının site kodu ile giriş yapılan site kodu eşleşmeli
                    login(request, user_to_auth)
                    return redirect('yonetim:panel')
                else:
                    messages.error(request, "Kullanıcı ve site bilgileri arasında tutarsızlık var.")
            else: messages.error(request, "Giriş bilgileri hatalı veya kullanıcı aktif değil/bulunamadı.")
        except Exception as e: messages.error(request, f"Giriş sırasında bir hata oluştu: {e}"); logger.error(f"Giriş hatası: {e}", exc_info=True)
        return render(request, 'yonetim/giris.html', {'form_data': form_data})
    return render(request, 'yonetim/giris.html', {'form_data': {}})


@transaction.atomic # kayit view'ı zaten yukarıda güncellendi, bu sadece yer tutucu
def kayit(request):
    # Yukarıda güncellenmiş kayit view'ı kullanılacak.
    # Bu fonksiyonun içeriği önceki mesajdaki güncel kayit fonksiyonu ile aynı olmalı.
    if request.user.is_authenticated:
        return redirect('yonetim:panel') 

    form_data = {}
    if request.method == 'POST':
        form_data = request.POST.copy() 
        password = form_data.get('password')
        password_confirm = form_data.get('password_confirm')
        first_name = form_data.get('first_name', '').strip()
        last_name = form_data.get('last_name', '').strip()
        role = form_data.get('rol')
        site_kodu_form = form_data.get('site_kodu', '').strip().upper()
        sakin_blok_id = form_data.get('sakin_blok_id')
        sakin_daire_id = form_data.get('sakin_daire_id')

        if not all([password, password_confirm, first_name, last_name, role, site_kodu_form]):
            messages.error(request, "Lütfen tüm zorunlu alanları doldurun.")
            return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        if password != password_confirm:
            messages.error(request, "Parolalar eşleşmiyor.")
            return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        if not re.match(r"^[A-Z0-9]{3}$", site_kodu_form):
             messages.error(request, "Site kodu 3 karakterli ve sadece büyük harf ve rakamlardan oluşmalıdır.")
             return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        try:
            generated_username = generate_unique_username(first_name, last_name, site_kodu_form)
        except Exception as e:
            messages.error(request, str(e))
            return render(request, 'yonetim/kayit.html', {'form_data': form_data})

        generated_email = f"{generated_username}@otomatikkayit.placeholder" 
        is_yonetici_flag = (role == 'yonetici')
        site_nesnesi = None

        if is_yonetici_flag:
            if Site.objects.filter(kod=site_kodu_form).exists():
                messages.error(request, f"'{site_kodu_form}' kodlu site zaten mevcut.")
                return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        else: 
            if not sakin_blok_id or not sakin_daire_id:
                messages.error(request, "Daire sakini olarak kayıt olmak için blok ve daire seçimi zorunludur.")
                if site_kodu_form: # Hata durumunda seçenekleri koru
                    try:
                        site_obj_for_options = Site.objects.get(kod=site_kodu_form)
                        form_data['sakin_blok_options'] = list(Blok.objects.filter(site=site_obj_for_options).order_by('ad').values('id', 'ad'))
                        if sakin_blok_id:
                            daireler_q = Daire.objects.filter(blok_id=sakin_blok_id, blok__site=site_obj_for_options, kullanici__isnull=True)
                            daireler_l_ajax = [{'id':d.id, 'no':str(d.daire_no), 'sort_key':d.get_sortable_daire_no()} for d in daireler_q]
                            daireler_s_d_ajax = sorted(daireler_l_ajax, key=lambda x: x['sort_key'])
                            form_data['sakin_daire_options'] = [{'id':d['id'], 'no':d['no']} for d in daireler_s_d_ajax]
                    except Site.DoesNotExist: pass
                return render(request, 'yonetim/kayit.html', {'form_data': form_data})
            try:
                site_nesnesi = Site.objects.get(kod=site_kodu_form)
            except Site.DoesNotExist:
                messages.error(request, f"'{site_kodu_form}' kodlu site bulunamadı.")
                return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        try:
            user = Kullanici.objects.create_user(username=generated_username, email=generated_email, password=password, first_name=first_name, last_name=last_name, site_kodu=site_kodu_form, is_yonetici=is_yonetici_flag)
            if is_yonetici_flag:
                messages.success(request, f"Yönetici olarak kayıt oldunuz. Lütfen '{site_kodu_form}' siteniz için bilgileri girin.")
                login(request, user); return redirect('yonetim:site_bilgi')
            else: 
                daire_nesnesi = Daire.objects.get(id=sakin_daire_id, blok_id=sakin_blok_id, blok__site=site_nesnesi)
                if daire_nesnesi.kullanici is not None:
                    user.delete() 
                    messages.error(request, f"Seçtiğiniz daire ({daire_nesnesi.daire_tam_adi}) dolu.")
                    # Seçenekleri koru
                    form_data['sakin_blok_options'] = list(Blok.objects.filter(site=site_nesnesi).order_by('ad').values('id', 'ad'))
                    daireler_q = Daire.objects.filter(blok_id=sakin_blok_id, blok__site=site_nesnesi, kullanici__isnull=True)
                    daireler_l_ajax = [{'id':d.id, 'no':str(d.daire_no), 'sort_key':d.get_sortable_daire_no()} for d in daireler_q]
                    daireler_s_d_ajax = sorted(daireler_l_ajax, key=lambda x: x['sort_key'])
                    form_data['sakin_daire_options'] = [{'id':d['id'], 'no':d['no']} for d in daireler_s_d_ajax]
                    return render(request, 'yonetim/kayit.html', {'form_data': form_data})
                daire_nesnesi.kullanici = user; daire_nesnesi.save()
                messages.success(request, f"{daire_nesnesi.daire_tam_adi} sakini olarak kayıt oldunuz.")
                login(request, user); return redirect('yonetim:panel')
        except Daire.DoesNotExist:
            if 'user' in locals() and hasattr(user, 'pk') and user.pk: user.delete()
            messages.error(request, "Seçilen daire veya blok geçersiz."); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        except IntegrityError: 
            if 'user' in locals() and hasattr(user, 'pk') and user.pk: user.delete()
            messages.error(request, "Kayıt hatası (IE)."); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        except Exception as e:
            logger.error(f"Kayıt Genel Hata: {e}", exc_info=True)
            if 'user' in locals() and hasattr(user, 'pk') and user.pk: user.delete()
            messages.error(request, f"Hata: {e}"); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
    else: 
        site_kodu_get = request.GET.get('sitekodu', '').strip().upper()
        form_data = {'site_kodu': site_kodu_get}
        if site_kodu_get:
            form_data['rol'] = 'sakin' 
            try:
                site_obj_for_options = Site.objects.get(kod=site_kodu_get)
                form_data['sakin_blok_options'] = list(Blok.objects.filter(site=site_obj_for_options).order_by('ad').values('id', 'ad'))
            except Site.DoesNotExist:
                if site_kodu_get: 
                    messages.warning(request, f"'{site_kodu_get}' site kodu ile site bulunamadı.")
                form_data['site_kodu'] = '' 
                form_data.pop('sakin_blok_options', None)
    return render(request, 'yonetim/kayit.html', {'form_data': form_data})


@login_required
def cikis(request):
    logout(request)
    messages.info(request, "Başarıyla çıkış yaptınız.")
    return redirect('yonetim:giris')

# AJAX view'ları @login_required OLMAMALI
def ajax_bloklar(request):
    site_kodu = request.GET.get('site_kodu','').strip().upper()
    if not site_kodu or not re.match(r"^[A-Z0-9]{3}$", site_kodu):
        return JsonResponse({'error':'Geçerli bir site kodu (3 karakter, büyük harf/rakam) gerekli.'}, status=400)
    try:
        site_obj = Site.objects.get(kod=site_kodu)
        bloklar_data = list(Blok.objects.filter(site=site_obj).order_by('ad').values('id','ad'))
        if not bloklar_data:
            return JsonResponse({'bloklar':[], 'message':'Bu sitede henüz blok tanımlanmamış.'})
        return JsonResponse({'bloklar': bloklar_data})
    except Site.DoesNotExist:
        return JsonResponse({'error':f"'{site_kodu}' kodlu site bulunamadı."}, status=404)
    except Exception as e:
        logger.error(f"AJAX Bloklar hatası: {e}", exc_info=True)
        return JsonResponse({'error':'Bloklar alınırken sunucu tarafında bir hata oluştu.'}, status=500)

def ajax_daireler(request):
    blok_id_str = request.GET.get('blok_id', None)
    if not blok_id_str:
        return JsonResponse({'error':'Blok ID gerekli.'}, status=400)
    try:
        blok_id = int(blok_id_str)
        blok_obj = Blok.objects.get(id=blok_id)
        daireler_queryset = Daire.objects.filter(blok=blok_obj, kullanici__isnull=True)
        daireler_list_for_sort = [{'id':d.id, 'no':str(d.daire_no), 'sort_key':d.get_sortable_daire_no()} for d in daireler_queryset]
        daireler_sorted_list = sorted(daireler_list_for_sort, key=lambda x: x['sort_key'])
        daireler_data = [{'id':d['id'], 'no':d['no']} for d in daireler_sorted_list]
        if not daireler_data:
            return JsonResponse({'daireler':[], 'message':'Bu blokta kayıt olunabilecek boş daire bulunmamaktadır.'})
        return JsonResponse({'daireler': daireler_data})
    except ValueError:
        return JsonResponse({'error':'Geçersiz Blok ID formatı.'}, status=400)
    except Blok.DoesNotExist:
        return JsonResponse({'error':'Belirtilen blok bulunamadı.'}, status=404)
    except Exception as e:
        logger.error(f"AJAX Daireler hatası: {e}", exc_info=True)
        return JsonResponse({'error':'Daireler alınırken sunucu tarafında bir hata oluştu.'}, status=500)

# Gider ve Aidat CRUD operasyonları için yetkilendirme kontrolleri eklendi/güncellendi
@login_required
def gider_update(request, gider_id):
    try:
        site_obj = Site.objects.get(kod=request.user.site_kodu)
        if not request.user.is_yonetici or request.user != site_obj.yonetici:
            messages.error(request, "Bu işlemi yapmak için yetkiniz yok.")
            return redirect('yonetim:panel')
    except Site.DoesNotExist:
        messages.error(request, "İlişkili site bulunamadı."); return redirect('yonetim:panel')
    gider_obj = get_object_or_404(Gider, id=gider_id, site=site_obj)
    if request.method == 'POST':
        form = GiderForm(request.POST, request.FILES, instance=gider_obj)
        if form.is_valid(): form.save(); messages.success(request, "Gider güncellendi."); return redirect(reverse('yonetim:panel') + '#siteGiderListesiPane')
    else: form = GiderForm(instance=gider_obj)
    return render(request, 'yonetim/gider_form.html', {'form': form, 'gider': gider_obj, 'site': site_obj})

@login_required
def gider_delete(request, gider_id):
    try:
        site_obj = Site.objects.get(kod=request.user.site_kodu)
        if not request.user.is_yonetici or request.user != site_obj.yonetici:
            messages.error(request, "Bu işlemi yapmak için yetkiniz yok."); return redirect('yonetim:panel')
    except Site.DoesNotExist: messages.error(request, "İlişkili site bulunamadı."); return redirect('yonetim:panel')
    gider_obj = get_object_or_404(Gider, id=gider_id, site=site_obj)
    if request.method == 'POST': gider_obj.delete(); messages.success(request, "Gider silindi."); return redirect(reverse('yonetim:panel') + '#siteGiderListesiPane')
    return render(request, 'yonetim/gider_confirm_delete.html', {'gider': gider_obj, 'site': site_obj})

@login_required
def aidat_update(request, aidat_id):
    aidat_obj = get_object_or_404(Aidat, id=aidat_id); daire_obj = aidat_obj.daire; site_obj = daire_obj.blok.site
    is_yonetici_of_site = request.user.is_yonetici and request.user.site_kodu == site_obj.kod
    if not is_yonetici_of_site: messages.error(request, "Aidat güncelleme yetkiniz yok."); return redirect('yonetim:panel') # Sadece yönetici güncelleyebilir
    if request.method == 'POST':
        form = AidatForm(request.POST, request.FILES, instance=aidat_obj)
        if form.is_valid(): form.save(); messages.success(request, "Aidat kaydı güncellendi."); return redirect(reverse('yonetim:panel') + '#daireAidatDetayListesiPane')
    else: form = AidatForm(instance=aidat_obj)
    return render(request, 'yonetim/aidat_form.html', {'form': form, 'aidat': aidat_obj, 'daire': daire_obj, 'site': site_obj})

@login_required
def aidat_delete(request, aidat_id):
    aidat_obj = get_object_or_404(Aidat, id=aidat_id); daire_obj = aidat_obj.daire; site_obj = daire_obj.blok.site
    is_yonetici_of_site = request.user.is_yonetici and request.user.site_kodu == site_obj.kod
    if not is_yonetici_of_site: messages.error(request, "Aidat silme yetkiniz yok."); return redirect('yonetim:panel') # Sadece yönetici silebilir
    if request.method == 'POST': aidat_obj.delete(); messages.success(request, "Aidat kaydı silindi."); return redirect(reverse('yonetim:panel') + '#daireAidatDetayListesiPane')
    return render(request, 'yonetim/aidat_confirm_delete.html', {'aidat': aidat_obj, 'daire': daire_obj, 'site': site_obj})

@login_required
def daire_odeme_detay(request, daire_id):
    try: daire_obj = get_object_or_404(Daire.objects.select_related('blok__site', 'kullanici'), id=daire_id, blok__site__kod=request.user.site_kodu)
    except Daire.DoesNotExist: messages.error(request, "Daire bulunamadı."); return redirect('yonetim:panel')
    is_yonetici_of_site = request.user.is_yonetici and request.user.site_kodu == daire_obj.blok.site.kod
    is_sakin_of_daire = daire_obj.kullanici == request.user
    if not (is_yonetici_of_site or is_sakin_of_daire): messages.error(request, "Bu detayı görüntüleme yetkiniz yok."); return redirect('yonetim:panel')
    
    site_obj = daire_obj.blok.site
    aidatlar = Aidat.objects.filter(daire=daire_obj).order_by('-tarih', '-id')
    # ... (kalan hesaplamalar aynı) ...
    toplam_odenen_aidat_tum_zamanlar = aidatlar.aggregate(toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    current_year = timezone.now().year
    yillik_aidat_borcu_daire = (site_obj.aidat_miktari * 12) if site_obj.aidat_miktari else Decimal('0.00')
    start_of_year = date(current_year, 1, 1); end_of_year = date(current_year, 12, 31)
    odenen_bu_yil_daire = Aidat.objects.filter(daire=daire_obj, tarih__gte=start_of_year, tarih__lte=end_of_year).aggregate(toplam_odenen=Sum('tutar'))['toplam_odenen'] or Decimal('0.00')
    bakiye_bu_yil_daire = odenen_bu_yil_daire - yillik_aidat_borcu_daire
    context = {
        'daire_nesnesi': daire_obj, 'aidatlar': aidatlar, 'toplam_odenen_tum_zamanlar_daire': toplam_odenen_aidat_tum_zamanlar,
        'yillik_aidat_borcu_daire': yillik_aidat_borcu_daire, 'odenen_bu_yil_daire': odenen_bu_yil_daire,
        'bakiye_bu_yil_daire': bakiye_bu_yil_daire, 'current_year': current_year, 'site': site_obj,
        'can_edit_aidat_records': is_yonetici_of_site, # Aidat kayıtlarını sadece yönetici düzenleyip silebilsin
    }
    return render(request, 'yonetim/daire_odeme_detay.html', context)