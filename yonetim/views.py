# yonetim/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from .models import Site, Kullanici, Blok, Daire, Aidat, Gider
from django.contrib import messages
from django.http import JsonResponse
import re
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.utils import timezone
from datetime import date
import logging
import uuid
from django.urls import reverse
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
    char_map = { 'ı': 'i', 'İ': 'I', 'ö': 'o', 'Ö': 'O', 'ü': 'u', 'Ü': 'U', 'ş': 's', 'Ş': 'S', 'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G' }
    clean_first_name = first_name.lower(); clean_last_name = last_name.lower()
    for tr_char, en_char in char_map.items():
        clean_first_name = clean_first_name.replace(tr_char, en_char)
        clean_last_name = clean_last_name.replace(tr_char, en_char)
    clean_first_name = re.sub(r'\W+', '', clean_first_name); clean_last_name = re.sub(r'\W+', '', clean_last_name)
    base_username = f"{clean_first_name}{clean_last_name}_{site_kodu.lower()}"
    max_len_for_base = 150 - (len(site_kodu) + 1 + 6)
    username_candidate = base_username[:max_len_for_base]
    final_username_try = f"{username_candidate}_{uuid.uuid4().hex[:6]}"
    counter = 1
    original_username_base_for_retry = username_candidate
    while Kullanici.objects.filter(username=final_username_try).exists():
        new_uuid_part = uuid.uuid4().hex[:6]
        if counter > 5 and len(original_username_base_for_retry) > 5: original_username_base_for_retry = original_username_base_for_retry[:-1]
        max_len_for_base_retry = 150 - (len(site_kodu) + 1 + len(new_uuid_part))
        final_username_try = f"{original_username_base_for_retry[:max_len_for_base_retry]}_{new_uuid_part}"
        counter += 1
        if counter > 100: raise Exception("Benzersiz kullanıcı adı üretilemedi.")
    return final_username_try[:150]

# --- PANEL VIEW ---
@login_required
def panel(request):
    try:
        site_obj = Site.objects.get(kod=request.user.site_kodu)
    except Site.DoesNotExist:
        messages.error(request, "Site bulunamadı. Lütfen yöneticinize başvurun veya site kodunuzu kontrol edin.")
        logout(request)
        return redirect('yonetim:giris')

    kullanici_kendi_dairesi = get_user_daire(request.user, request.user.site_kodu)
    can_manage_site_finances = request.user.is_yonetici

    if request.method == 'POST':
        kayit_turu = request.POST.get('kayit_turu_select', 'aidat') # Yönetici değilse veya seçilmemişse varsayılan 'aidat'

        if kayit_turu == 'aidat':
            if not kullanici_kendi_dairesi:
                messages.error(request, "Aidat eklenecek bir daireniz bulunmamaktadır.")
                return redirect('yonetim:panel')
            
            form = AidatForm(request.POST, request.FILES)
            if form.is_valid():
                aidat = form.save(commit=False)
                aidat.daire = kullanici_kendi_dairesi
                if not aidat.tutar and site_obj.aidat_miktari: # Formda boşsa sitedeki standart aidatı al
                    aidat.tutar = site_obj.aidat_miktari
                aidat.save()
                messages.success(request, f"{kullanici_kendi_dairesi.daire_tam_adi} için aidat başarıyla eklendi.")
            else:
                for field, errors in form.errors.items():
                    messages.error(request, f"Aidat Formu Hatası ({form.fields[field].label if field in form.fields else field}): {'; '.join(errors)}")
        
        elif kayit_turu == 'gider' and can_manage_site_finances:
            form = GiderForm(request.POST, request.FILES, prefix="gider")
            if form.is_valid():
                gider = form.save(commit=False)
                gider.site = site_obj
                gider.save()
                messages.success(request, "Gider başarıyla eklendi.")
            else:
                for field, errors in form.errors.items():
                    messages.error(request, f"Gider Formu Hatası ({form.fields[field].label if field in form.fields else field}): {'; '.join(errors)}")
        
        elif kayit_turu == 'gider' and not can_manage_site_finances:
            messages.error(request, "Gider ekleme yetkiniz bulunmamaktadır.")

        return redirect('yonetim:panel') # Her POST sonrası panele dön

    # GET request için context hazırlığı
    toplam_gelir_aidatlar = Aidat.objects.filter(daire__blok__site=site_obj).aggregate(toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    toplam_giderler = Gider.objects.filter(site=site_obj).aggregate(toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    kasa_bakiyesi = toplam_gelir_aidatlar - toplam_giderler

    daire_aidat_ozetleri_listesi = []
    tum_daireler_qs = Daire.objects.filter(blok__site=site_obj).select_related('blok', 'kullanici')
    daireler_listesi_sirali = sorted(list(tum_daireler_qs), key=daire_natural_sort_key)
    current_year = timezone.now().year
    yillik_aidat_borcu_bir_daire_icin = (site_obj.aidat_miktari * 12) if site_obj.aidat_miktari else Decimal('0.00')
    kullanici_mevcut_bakiye_panel = None

    for daire_obj_loop in daireler_listesi_sirali:
        start_of_year = date(current_year, 1, 1); end_of_year = date(current_year, 12, 31)
        bu_yil_odenen_tutar = Aidat.objects.filter(daire=daire_obj_loop, tarih__gte=start_of_year, tarih__lte=end_of_year).aggregate(toplam_odenen=Sum('tutar'))['toplam_odenen'] or Decimal('0.00')
        bakiye = bu_yil_odenen_tutar - yillik_aidat_borcu_bir_daire_icin
        daire_aidat_ozetleri_listesi.append({
            'daire': daire_obj_loop, 'blok_daire': f"{daire_obj_loop.blok.ad.upper()} - {daire_obj_loop.daire_no}",
            'sakin': daire_obj_loop.kullanici.get_full_name() if daire_obj_loop.kullanici else "Boş",
            'yillik_borc': yillik_aidat_borcu_bir_daire_icin, 'odenen': bu_yil_odenen_tutar, 'bakiye': bakiye,
            'is_current_user_flat': kullanici_kendi_dairesi and daire_obj_loop.id == kullanici_kendi_dairesi.id })
        if kullanici_kendi_dairesi and daire_obj_loop.id == kullanici_kendi_dairesi.id:
            kullanici_mevcut_bakiye_panel = bakiye

    if not request.user.is_yonetici and kullanici_kendi_dairesi:
        kendi_daire_ozeti = next((item for item in daire_aidat_ozetleri_listesi if item['daire'].id == kullanici_kendi_dairesi.id), None)
        if kendi_daire_ozeti: daire_aidat_ozetleri_listesi.remove(kendi_daire_ozeti); daire_aidat_ozetleri_listesi.insert(0, kendi_daire_ozeti)

    site_gider_listesi_data = Gider.objects.filter(site=site_obj).order_by('-tarih', '-id')[:50]
    daire_aidat_detay_listesi_data = Aidat.objects.filter(daire__blok__site=site_obj).select_related('daire__blok', 'daire__kullanici').order_by('-tarih', '-id')[:50]

    aidat_form_initial = {'tarih': timezone.now().date()}
    if site_obj.aidat_miktari: aidat_form_initial['tutar'] = site_obj.aidat_miktari
    
    # Her zaman iki formu da oluştur, şablonda gösterilip gösterilmeyeceğine karar verilir.
    aidat_form_instance = AidatForm(initial=aidat_form_initial, prefix="aidat") # Prefix eklendi
    gider_form_instance = GiderForm(initial={'tarih': timezone.now().date()}, prefix="gider") # Prefix eklendi

    context = {
        'site': site_obj, 'kullanici_kendi_dairesi': kullanici_kendi_dairesi, 'current_year': current_year,
        'toplam_gelir_aidatlar': toplam_gelir_aidatlar, 'toplam_giderler': toplam_giderler, 'kasa_bakiyesi': kasa_bakiyesi,
        'aidat_listesi_ozet': daire_aidat_ozetleri_listesi,
        'site_gider_listesi': site_gider_listesi_data,
        'daire_aidat_detay_listesi': daire_aidat_detay_listesi_data,
        'aidat_form_panel': aidat_form_instance, # Yeni isim
        'gider_form_panel': gider_form_instance, # Yeni isim
        'can_manage_site_finances': can_manage_site_finances,
        'kullanici_mevcut_bakiye_panel': kullanici_mevcut_bakiye_panel,
    }
    return render(request, 'yonetim/panel.html', context)

# --- GİRİŞ, KAYIT, ÇIKIŞ ---
def giris(request):
    if request.user.is_authenticated:
        return redirect('yonetim:panel')
    
    form_data = request.POST.copy() if request.method == 'POST' else {}

    if request.method == 'POST':
        first_name = form_data.get('first_name', '').strip()
        last_name = form_data.get('last_name', '').strip()
        site_kodu_giris = form_data.get('site_kodu', '').strip().upper()
        blok_id_giris_str = form_data.get('blok_id_giris') # Artık ID gelecek
        daire_id_giris_str = form_data.get('daire_id_giris') # Artık ID gelecek
        password_giris = form_data.get('password')

        if not all([first_name, last_name, site_kodu_giris, blok_id_giris_str, daire_id_giris_str, password_giris]):
            messages.error(request, "Tüm alanların doldurulması zorunludur.")
        else:
            try:
                # Daireyi ID'ler ile bul
                daire_obj = Daire.objects.select_related('kullanici', 'blok__site').get(
                    id=int(daire_id_giris_str),
                    blok_id=int(blok_id_giris_str),
                    blok__site__kod=site_kodu_giris,
                    kullanici__is_active=True,
                    kullanici__first_name__iexact=first_name,
                    kullanici__last_name__iexact=last_name
                )
                if daire_obj.kullanici:
                    user_to_auth = authenticate(request, username=daire_obj.kullanici.username, password=password_giris)
                    if user_to_auth is not None:
                        if user_to_auth.site_kodu.upper() == site_kodu_giris:
                            login(request, user_to_auth)
                            return redirect('yonetim:panel')
                        else:
                            messages.error(request, "Kullanıcı ve site bilgileri arasında uyuşmazlık var.")
                    else:
                        messages.error(request, "Parola hatalı veya kullanıcı bulunamadı.")
                else: # Daire var ama sakini yoksa (bu durum login için mantıksız)
                    messages.error(request, "Belirtilen dairede aktif bir sakin kaydı bulunamadı.")
            except (Daire.DoesNotExist, ValueError):
                messages.error(request, "Giriş bilgileri hatalı veya eksik.")
            except Exception as e:
                messages.error(request, f"Giriş sırasında bir hata oluştu: {e}")
                logger.error(f"Giriş hatası: {e}", exc_info=True)
        
        # Hata durumunda veya GET isteğinde dropdown'ları doldurmak için context hazırla
        if site_kodu_giris:
            try:
                site_obj_for_options = Site.objects.get(kod=site_kodu_giris)
                form_data['blok_options_giris'] = list(Blok.objects.filter(site=site_obj_for_options).order_by('ad').values('id', 'ad'))
                if blok_id_giris_str:
                    form_data['secili_blok_id_giris'] = blok_id_giris_str # String olarak sakla
                    blok_for_daire_options = Blok.objects.get(id=int(blok_id_giris_str), site=site_obj_for_options)
                    # Giriş için TÜM daireleri listele
                    daireler_q = Daire.objects.filter(blok=blok_for_daire_options)
                    daireler_l_ajax = [{'id':d.id, 'no':str(d.daire_no), 'sort_key':d.get_sortable_daire_no()} for d in daireler_q]
                    daireler_s_d_ajax = sorted(daireler_l_ajax, key=lambda x: x['sort_key'])
                    form_data['daire_options_giris'] = [{'id':d['id'], 'no':d['no']} for d in daireler_s_d_ajax]
                    if daire_id_giris_str:
                        form_data['secili_daire_id_giris'] = daire_id_giris_str # String olarak sakla
            except (Site.DoesNotExist, Blok.DoesNotExist, ValueError):
                form_data.pop('blok_options_giris', None) # Hata varsa seçenekleri temizle
                form_data.pop('daire_options_giris', None)
        return render(request, 'yonetim/giris.html', {'form_data': form_data})

    # İlk GET isteği için
    return render(request, 'yonetim/giris.html', {'form_data': {}})


@transaction.atomic
def kayit(request): # Bu fonksiyonun içeriği önceki halleriyle büyük ölçüde aynı kalabilir.
    if request.user.is_authenticated: return redirect('yonetim:panel')
    # ... (önceki cevaplardaki tam kayit fonksiyonu buraya gelecek) ...
    # Önemli: kayit.html'deki AJAX çağrıları ajax_bloklar ve ajax_daireler'i kullanmaya devam edecek.
    # ajax_daireler fonksiyonu 'list_all' parametresi almadığında sadece boş daireleri listeler.
    form_data = {}
    if request.method == 'POST':
        form_data = request.POST.copy()
        password = form_data.get('password'); password_confirm = form_data.get('password_confirm')
        first_name = form_data.get('first_name', '').strip(); last_name = form_data.get('last_name', '').strip()
        role = form_data.get('rol'); site_kodu_form = form_data.get('site_kodu', '').strip().upper()
        sakin_blok_id = form_data.get('sakin_blok_id'); sakin_daire_id = form_data.get('sakin_daire_id')
        if not all([password, password_confirm, first_name, last_name, role, site_kodu_form]):
            messages.error(request, "Lütfen tüm zorunlu alanları doldurun."); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        if password != password_confirm:
            messages.error(request, "Parolalar eşleşmiyor."); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        if not re.match(r"^[A-Z0-9]{3}$", site_kodu_form):
             messages.error(request, "Site kodu 3 karakterli ve sadece büyük harf ve rakamlardan oluşmalıdır."); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        try: generated_username = generate_unique_username(first_name, last_name, site_kodu_form)
        except Exception as e: messages.error(request, str(e)); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        generated_email = f"{generated_username}@otomatikkayit.placeholder"
        is_yonetici_flag = (role == 'yonetici'); site_nesnesi = None
        if is_yonetici_flag:
            if Site.objects.filter(kod=site_kodu_form).exists():
                messages.error(request, f"'{site_kodu_form}' kodlu site mevcut."); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        else:
            if not sakin_blok_id or not sakin_daire_id:
                messages.error(request, "Sakin için blok ve daire seçimi zorunludur.");
                # Hata durumunda seçenekleri koru
                if site_kodu_form:
                    try:
                        site_obj_for_options = Site.objects.get(kod=site_kodu_form)
                        form_data['sakin_blok_options'] = list(Blok.objects.filter(site=site_obj_for_options).order_by('ad').values('id', 'ad'))
                        if sakin_blok_id:
                            blok_for_daire_options = Blok.objects.get(id=int(sakin_blok_id))
                            daireler_q = Daire.objects.filter(blok=blok_for_daire_options, kullanici__isnull=True) # Sadece boş daireler
                            daireler_l_ajax = [{'id':d.id, 'no':str(d.daire_no), 'sort_key':d.get_sortable_daire_no()} for d in daireler_q]
                            daireler_s_d_ajax = sorted(daireler_l_ajax, key=lambda x: x['sort_key'])
                            form_data['sakin_daire_options'] = [{'id':d['id'], 'no':d['no']} for d in daireler_s_d_ajax]
                    except (Site.DoesNotExist, Blok.DoesNotExist, ValueError) : pass
                return render(request, 'yonetim/kayit.html', {'form_data': form_data})
            try: site_nesnesi = Site.objects.get(kod=site_kodu_form)
            except Site.DoesNotExist: messages.error(request, f"'{site_kodu_form}' kodlu site bulunamadı."); return render(request, 'yonetim/kayit.html', {'form_data': form_data})

        user = None
        try:
            user = Kullanici.objects.create_user(username=generated_username, email=generated_email, password=password, first_name=first_name, last_name=last_name, site_kodu=site_kodu_form, is_yonetici=is_yonetici_flag)
            if is_yonetici_flag:
                login(request, user); messages.success(request, f"Yönetici olarak kayıt oldunuz. '{site_kodu_form}' için site bilgilerinizi girin."); return redirect('yonetim:site_bilgi')
            else:
                daire_nesnesi = Daire.objects.get(id=int(sakin_daire_id), blok_id=int(sakin_blok_id), blok__site=site_nesnesi)
                if daire_nesnesi.kullanici is not None:
                    user.delete(); messages.error(request, f"{daire_nesnesi.daire_tam_adi} dolu."); return render(request, 'yonetim/kayit.html', {'form_data': form_data})
                daire_nesnesi.kullanici = user; daire_nesnesi.save()
                login(request, user); messages.success(request, f"Sakin olarak kayıt oldunuz: {daire_nesnesi.daire_tam_adi}"); return redirect('yonetim:panel')
        except Daire.DoesNotExist:
            if user: user.delete(); messages.error(request, "Seçilen daire/blok geçersiz.");
        except IntegrityError:
            if user: user.delete(); messages.error(request, "Kayıt hatası (IE).");
        except ValueError: # int() çevirme hatası
             if user: user.delete(); messages.error(request, "Blok veya daire ID hatası.");
        except Exception as e:
            if user: user.delete()
            logger.error(f"Kayıt Genel Hata: {e}", exc_info=True); messages.error(request, f"Hata: {e}");
        form_data.pop('password', None); form_data.pop('password_confirm', None)
        return render(request, 'yonetim/kayit.html', {'form_data': form_data})
    else: # GET
        site_kodu_get = request.GET.get('sitekodu', '').strip().upper()
        form_data = {'site_kodu': site_kodu_get}
        if site_kodu_get:
            form_data['rol'] = 'sakin'
            try:
                site_obj_for_options = Site.objects.get(kod=site_kodu_get)
                form_data['sakin_blok_options'] = list(Blok.objects.filter(site=site_obj_for_options).order_by('ad').values('id', 'ad'))
            except Site.DoesNotExist:
                if site_kodu_get: messages.warning(request, f"'{site_kodu_get}' site kodu ile site bulunamadı.")
                form_data['site_kodu'] = ''; form_data.pop('sakin_blok_options', None)
        else: form_data['rol'] = 'sakin' # Varsayılan
    return render(request, 'yonetim/kayit.html', {'form_data': form_data})

@login_required
def cikis(request):
    logout(request)
    messages.info(request, "Başarıyla çıkış yaptınız.")
    return redirect('yonetim:giris')

# --- AJAX FONKSİYONLARI ---
# ajax_bloklar (Kayıt ve Giriş için ortak kullanılabilir)
# @login_required OLMAMALI, çünkü kayıt/giriş sırasında henüz login olunmamış olabilir.
def ajax_bloklar(request):
    site_kodu = request.GET.get('site_kodu','').strip().upper()
    if not site_kodu or not re.match(r"^[A-Z0-9]{3}$", site_kodu):
        return JsonResponse({'error':'Geçerli bir site kodu girin.'}, status=400)
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
        return JsonResponse({'error':'Sunucu hatası.'}, status=500)

# ajax_daireler (Kayıt için sadece boş daireler, Giriş için tüm daireler)
# @login_required OLMAMALI
def ajax_daireler(request):
    blok_id_str = request.GET.get('blok_id', None)
    list_all_str = request.GET.get('list_all', 'false').lower() # Giriş sayfası 'true' gönderecek

    if not blok_id_str:
        return JsonResponse({'error':'Blok ID gerekli.'}, status=400)
    try:
        blok_id = int(blok_id_str)
        blok_obj = Blok.objects.get(id=blok_id)
        
        daireler_queryset = Daire.objects.filter(blok=blok_obj)
        if list_all_str != 'true': # Kayıt için sadece boş daireler
            daireler_queryset = daireler_queryset.filter(kullanici__isnull=True)
        
        daireler_list_for_sort = [{'id':d.id, 'no':str(d.daire_no), 'sort_key':d.get_sortable_daire_no()} for d in daireler_queryset]
        daireler_sorted_list = sorted(daireler_list_for_sort, key=lambda x: x['sort_key'])
        daireler_data = [{'id':d['id'], 'no':d['no']} for d in daireler_sorted_list]

        if not daireler_data:
            message = 'Bu blokta daire bulunmamaktadır.'
            if list_all_str != 'true':
                message = 'Bu blokta kayıt olunabilecek boş daire bulunmamaktadır.'
            return JsonResponse({'daireler':[], 'message': message})
        return JsonResponse({'daireler': daireler_data})
    except ValueError:
        return JsonResponse({'error':'Geçersiz Blok ID.'}, status=400)
    except Blok.DoesNotExist:
        return JsonResponse({'error':'Blok bulunamadı.'}, status=404)
    except Exception as e:
        logger.error(f"AJAX Daireler hatası: {e}", exc_info=True)
        return JsonResponse({'error':'Sunucu hatası.'}, status=500)

# --- SİTE BİLGİLERİ, AİDAT/GİDER CRUD vb. ---
# Bu fonksiyonların tam halleri önceki cevaplarda mevcut.
# @login_required ve yetkilendirme kontrolleri içermelidirler.
@login_required
@transaction.atomic
def site_bilgi(request):
    # ... (önceki cevaplardaki tam site_bilgi fonksiyonu) ...
    is_read_only_mode = not request.user.is_yonetici; site_obj = None
    try: site_obj = Site.objects.select_related('yonetici').get(kod=request.user.site_kodu)
    except Site.DoesNotExist:
        if request.user.is_yonetici: pass
        else: messages.error(request, "İlişkili site bulunamadı."); return redirect('yonetim:panel')
    if site_obj is None and not request.user.is_yonetici: messages.error(request, "Site bilgileri görüntülenemiyor."); return redirect('yonetim:panel')
    if site_obj and not site_obj.yonetici and request.user.is_yonetici: site_obj.yonetici = request.user; site_obj.save()
    # ... (POST ve GET context hazırlığı önceki cevaplardaki gibi) ...
    yoneticinin_mevcut_dairesi = Daire.objects.filter(blok__site=site_obj, kullanici=request.user).first() if site_obj and request.user.is_yonetici else None
    form_data_initial = {}
    if site_obj: form_data_initial = {'ad': site_obj.ad, 'adres': site_obj.adres, 'yonetici_tel': site_obj.yonetici_tel, 'aidat_miktari': site_obj.aidat_miktari, 'kod': site_obj.kod}
    elif request.user.is_yonetici: form_data_initial['kod'] = request.user.site_kodu
    if request.method == 'POST':
        if is_read_only_mode: messages.error(request, "Bu sayfada değişiklik yapma yetkiniz yok."); return redirect('yonetim:site_bilgi')
        # ... (POST işleme mantığı önceki gibi) ...
        return redirect('yonetim:panel') # Başarılı POST sonrası
    # GET context
    bloklar_ve_daireleri_c = []
    sitedeki_bos_ve_yoneticiye_ait_d = []
    if site_obj:
        for blok_db in site_obj.bloklar.all().order_by('ad'): bloklar_ve_daireleri_c.append({'id': blok_db.id, 'ad': blok_db.ad, 'daire_sayisi': blok_db.daireler.count()})
        if request.user.is_yonetici:
            daire_q_for_yonetici_select = Daire.objects.filter(blok__site=site_obj).select_related('blok', 'kullanici')
            temp_d_list_for_yonetici_select = sorted(list(daire_q_for_yonetici_select), key=daire_natural_sort_key)
            for d_item in temp_d_list_for_yonetici_select:
                if d_item.kullanici is None or d_item.kullanici == request.user: sitedeki_bos_ve_yoneticiye_ait_d.append(d_item)
    context = { 'site': site_obj, 'bloklar_ve_daireleri': bloklar_ve_daireleri_c, 'is_yeni_site': site_obj is None and request.user.is_yonetici,
        'form_data': form_data_initial, 'is_read_only': is_read_only_mode,
        'sitedeki_bos_ve_yoneticiye_ait_daireler': sitedeki_bos_ve_yoneticiye_ait_d,
        'yoneticinin_mevcut_dairesi_id': yoneticinin_mevcut_dairesi.id if yoneticinin_mevcut_dairesi else None, }
    return render(request, 'yonetim/site_bilgi.html', context)


@login_required
def gider_update(request, gider_id):
    # ... (önceki cevaplardaki tam fonksiyon) ...
    try: site_obj = Site.objects.get(kod=request.user.site_kodu)
    except Site.DoesNotExist: messages.error(request, "İlişkili site bulunamadı."); return redirect('yonetim:panel')
    if not request.user.is_yonetici or request.user != site_obj.yonetici : messages.error(request, "Yetkiniz yok."); return redirect('yonetim:panel')
    gider_obj = get_object_or_404(Gider, id=gider_id, site=site_obj)
    if request.method == 'POST':
        form = GiderForm(request.POST, request.FILES, instance=gider_obj)
        if form.is_valid(): form.save(); messages.success(request, "Gider güncellendi."); return redirect(reverse('yonetim:panel') + '#siteGiderListesiPane')
    else: form = GiderForm(instance=gider_obj)
    return render(request, 'yonetim/gider_form.html', {'form': form, 'gider': gider_obj, 'site': site_obj})

@login_required
def gider_delete(request, gider_id):
    # ... (önceki cevaplardaki tam fonksiyon) ...
    try: site_obj = Site.objects.get(kod=request.user.site_kodu)
    except Site.DoesNotExist: messages.error(request, "İlişkili site bulunamadı."); return redirect('yonetim:panel')
    if not request.user.is_yonetici or request.user != site_obj.yonetici : messages.error(request, "Yetkiniz yok."); return redirect('yonetim:panel')
    gider_obj = get_object_or_404(Gider, id=gider_id, site=site_obj)
    if request.method == 'POST': gider_obj.delete(); messages.success(request, "Gider silindi."); return redirect(reverse('yonetim:panel') + '#siteGiderListesiPane')
    return render(request, 'yonetim/gider_confirm_delete.html', {'gider': gider_obj, 'site': site_obj})

@login_required
def aidat_update(request, aidat_id):
    # ... (önceki cevaplardaki tam fonksiyon) ...
    aidat_obj = get_object_or_404(Aidat, id=aidat_id); daire_obj = aidat_obj.daire; site_obj_of_aidat = daire_obj.blok.site
    if not (request.user.is_yonetici and request.user.site_kodu == site_obj_of_aidat.kod): messages.error(request, "Yetkiniz yok."); return redirect('yonetim:panel')
    if request.method == 'POST':
        form = AidatForm(request.POST, request.FILES, instance=aidat_obj)
        if form.is_valid(): form.save(); messages.success(request, "Aidat güncellendi.");
        return redirect((reverse('yonetim:panel') + '#daireAidatDetayListesiPane') if 'from_panel' in request.GET else reverse('yonetim:daire_odeme_detay', kwargs={'daire_id': daire_obj.id}))
    else: form = AidatForm(instance=aidat_obj)
    return render(request, 'yonetim/aidat_form.html', {'form': form, 'aidat': aidat_obj, 'daire': daire_obj, 'site': site_obj_of_aidat})


@login_required
def aidat_delete(request, aidat_id):
    # ... (önceki cevaplardaki tam fonksiyon) ...
    aidat_obj = get_object_or_404(Aidat, id=aidat_id); daire_obj = aidat_obj.daire; site_obj_of_aidat = daire_obj.blok.site
    if not (request.user.is_yonetici and request.user.site_kodu == site_obj_of_aidat.kod): messages.error(request, "Yetkiniz yok."); return redirect('yonetim:panel')
    if request.method == 'POST': aidat_obj.delete(); messages.success(request, "Aidat silindi.");
    return redirect((reverse('yonetim:panel') + '#daireAidatDetayListesiPane') if 'from_panel' in request.GET else reverse('yonetim:daire_odeme_detay', kwargs={'daire_id': daire_obj.id}))
    return render(request, 'yonetim/aidat_confirm_delete.html', {'aidat': aidat_obj, 'daire': daire_obj, 'site': site_obj_of_aidat})

@login_required
def daire_odeme_detay(request, daire_id):
    # ... (önceki cevaplardaki tam fonksiyon) ...
    try: daire_obj = get_object_or_404(Daire.objects.select_related('blok__site', 'kullanici'), id=daire_id, blok__site__kod=request.user.site_kodu)
    except: messages.error(request, "Daire bulunamadı."); return redirect('yonetim:panel')
    is_yonetici = request.user.is_yonetici and request.user.site_kodu == daire_obj.blok.site.kod
    is_sakin = daire_obj.kullanici == request.user
    if not (is_yonetici or is_sakin): messages.error(request, "Yetkiniz yok."); return redirect('yonetim:panel')
    site_obj = daire_obj.blok.site; aidatlar = Aidat.objects.filter(daire=daire_obj).order_by('-tarih', '-id')
    toplam_odenen_tum = aidatlar.aggregate(toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    current_year = timezone.now().year
    yillik_borc = (site_obj.aidat_miktari * 12) if site_obj.aidat_miktari else Decimal('0.00')
    odenen_bu_yil = Aidat.objects.filter(daire=daire_obj, tarih__year=current_year).aggregate(toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    bakiye_bu_yil = odenen_bu_yil - yillik_borc
    context = { 'daire_nesnesi': daire_obj, 'aidatlar': aidatlar, 'toplam_odenen_tum_zamanlar_daire': toplam_odenen_tum,
        'yillik_aidat_borcu_daire': yillik_borc, 'odenen_bu_yil_daire': odenen_bu_yil, 'bakiye_bu_yil_daire': bakiye_bu_yil,
        'current_year': current_year, 'site': site_obj, 'can_edit_aidat_records_on_detail': is_yonetici, }
    return render(request, 'yonetim/daire_odeme_detay.html', context)