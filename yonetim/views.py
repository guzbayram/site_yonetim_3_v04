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
# forms.py dosyanızda bu formların tanımlı olduğunu varsayıyoruz
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
    char_map = {'ı': 'i', 'İ': 'I', 'ö': 'o', 'Ö': 'O', 'ü': 'u',
                'Ü': 'U', 'ş': 's', 'Ş': 'S', 'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G'}
    clean_first_name = first_name.lower()
    clean_last_name = last_name.lower()
    for tr_char, en_char in char_map.items():
        clean_first_name = clean_first_name.replace(tr_char, en_char)
        clean_last_name = clean_last_name.replace(tr_char, en_char)
    clean_first_name = re.sub(r'\W+', '', clean_first_name)
    clean_last_name = re.sub(r'\W+', '', clean_last_name)
    base_username = f"{clean_first_name}{clean_last_name}_{site_kodu.lower()}"
    max_len_for_base = 150 - (len(site_kodu) + 1 + 6)
    username_candidate = base_username[:max_len_for_base]
    final_username_try = f"{username_candidate}_{uuid.uuid4().hex[:6]}"
    counter = 1
    original_username_base_for_retry = username_candidate
    while Kullanici.objects.filter(username=final_username_try).exists():
        new_uuid_part = uuid.uuid4().hex[:6]
        if counter > 5 and len(original_username_base_for_retry) > 5:
            original_username_base_for_retry = original_username_base_for_retry[:-1]
        max_len_for_base_retry = 150 - \
            (len(site_kodu) + 1 + len(new_uuid_part))
        final_username_try = f"{original_username_base_for_retry[:max_len_for_base_retry]}_{new_uuid_part}"
        counter += 1
        if counter > 100:
            raise Exception("Benzersiz kullanıcı adı üretilemedi.")
    return final_username_try[:150]

# --- PANEL VIEW ---


@login_required
def panel(request):
    try:
        site_obj = Site.objects.get(kod=request.user.site_kodu)
    except Site.DoesNotExist:
        messages.error(
            request, "Site bulunamadı. Lütfen yöneticinize başvurun veya site kodunuzu kontrol edin.")
        logout(request)
        return redirect('yonetim:giris')

    kullanici_kendi_dairesi = get_user_daire(
        request.user, request.user.site_kodu)
    can_manage_site_finances = request.user.is_yonetici

    # Formları her zaman oluştur, prefix'leri burada da belirtiyoruz.
    aidat_form_initial = {'tarih': timezone.now().date()}
    if site_obj.aidat_miktari:
        aidat_form_initial['tutar'] = site_obj.aidat_miktari

    aidat_form_instance = AidatForm(initial=aidat_form_initial, prefix="aidat")
    gider_form_instance = GiderForm(
        initial={'tarih': timezone.now().date()}, prefix="gider")

    if request.method == 'POST':
        # kayit_turu_select sadece yönetici için HTML'de olacağından, yönetici değilse None döner.
        # Yönetici değilse veya alan gönderilmemişse varsayılan 'aidat' olur.
        kayit_turu = request.POST.get('kayit_turu_select')

        # Eğer kullanıcı yönetici değilse, kayit_turu ne olursa olsun 'aidat' olarak kabul et
        if not can_manage_site_finances:
            kayit_turu = 'aidat'
        # Yönetici ama bir şekilde kayit_turu_select gelmemişse (formda sorun olabilir)
        elif not kayit_turu:
            kayit_turu = 'aidat'  # Güvenlik için varsayılan

        if kayit_turu == 'aidat':
            if not kullanici_kendi_dairesi:
                messages.error(
                    request, "Aidat eklenecek bir daireniz bulunmamaktadır.")
                return redirect('yonetim:panel')

            form_aidat_posted = AidatForm(
                request.POST, request.FILES, prefix="aidat")
            if form_aidat_posted.is_valid():
                aidat = form_aidat_posted.save(commit=False)
                aidat.daire = kullanici_kendi_dairesi
                if not aidat.tutar and site_obj.aidat_miktari:
                    aidat.tutar = site_obj.aidat_miktari
                aidat.save()
                messages.success(
                    request, f"{kullanici_kendi_dairesi.daire_tam_adi} için aidat başarıyla eklendi.")
                # Başarılı işlem sonrası formu temizlemek için redirect
                return redirect('yonetim:panel')
            else:
                # Aidat formu hatalarını sakla ve GET request'e geçer gibi context'i yeniden kur
                aidat_form_instance = form_aidat_posted  # Hatalı formu tekrar göster
                for field_name, errors in form_aidat_posted.errors.items():
                    # field_name 'aidat-tutar' gibi prefixli gelecektir
                    # form['prefix-alan_adi'] şeklinde erişim
                    field_obj = form_aidat_posted[field_name]
                    field_label = field_obj.label if hasattr(
                        field_obj, 'label') else field_name
                    messages.error(
                        request, f"Aidat Formu Hatası ({field_label}): {'; '.join(errors)}")

        elif kayit_turu == 'gider':  # can_manage_site_finances zaten yukarıda kontrol edildi
            form_gider_posted = GiderForm(
                request.POST, request.FILES, prefix="gider")
            if form_gider_posted.is_valid():
                gider = form_gider_posted.save(commit=False)
                gider.site = site_obj
                gider.save()
                messages.success(request, "Gider başarıyla eklendi.")
                # Başarılı işlem sonrası formu temizlemek için redirect
                return redirect('yonetim:panel')
            else:
                # Gider formu hatalarını sakla
                gider_form_instance = form_gider_posted  # Hatalı formu tekrar göster
                for field_name, errors in form_gider_posted.errors.items():
                    field_obj = form_gider_posted[field_name]
                    field_label = field_obj.label if hasattr(
                        field_obj, 'label') else field_name
                    messages.error(
                        request, f"Gider Formu Hatası ({field_label}): {'; '.join(errors)}")

        # Eğer POST sonrası redirect yapılmadıysa (yani form valid değilse),
        # hatalı form instance'ları zaten ayarlandı, sayfa aşağıda yeniden render edilecek.

    # GET request veya hatalı POST sonrası context hazırlığı
    toplam_gelir_aidatlar = Aidat.objects.filter(daire__blok__site=site_obj).aggregate(
        toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    toplam_giderler = Gider.objects.filter(site=site_obj).aggregate(
        toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    kasa_bakiyesi = toplam_gelir_aidatlar - toplam_giderler

    daire_aidat_ozetleri_listesi = []
    # ... (aidat özet listesi oluşturma kodunuz aynı kalabilir) ...
    tum_daireler_qs = Daire.objects.filter(
        blok__site=site_obj).select_related('blok', 'kullanici')
    daireler_listesi_sirali = sorted(
        list(tum_daireler_qs), key=daire_natural_sort_key)
    current_year = timezone.now().year
    yillik_aidat_borcu_bir_daire_icin = (
        site_obj.aidat_miktari * 12) if site_obj.aidat_miktari else Decimal('0.00')
    kullanici_mevcut_bakiye_panel = None

    for daire_obj_loop in daireler_listesi_sirali:
        start_of_year = date(current_year, 1, 1)
        end_of_year = date(current_year, 12, 31)
        bu_yil_odenen_tutar = Aidat.objects.filter(daire=daire_obj_loop, tarih__gte=start_of_year, tarih__lte=end_of_year).aggregate(
            toplam_odenen=Sum('tutar'))['toplam_odenen'] or Decimal('0.00')
        bakiye = bu_yil_odenen_tutar - yillik_aidat_borcu_bir_daire_icin
        daire_aidat_ozetleri_listesi.append({
            'daire': daire_obj_loop, 'blok_daire': f"{daire_obj_loop.blok.ad.upper()} - {daire_obj_loop.daire_no}",
            'sakin': daire_obj_loop.kullanici.get_full_name() if daire_obj_loop.kullanici else "Boş",
            'yillik_borc': yillik_aidat_borcu_bir_daire_icin, 'odenen': bu_yil_odenen_tutar, 'bakiye': bakiye,
            'is_current_user_flat': kullanici_kendi_dairesi and daire_obj_loop.id == kullanici_kendi_dairesi.id})
        if kullanici_kendi_dairesi and daire_obj_loop.id == kullanici_kendi_dairesi.id:
            kullanici_mevcut_bakiye_panel = bakiye

    if not request.user.is_yonetici and kullanici_kendi_dairesi:
        kendi_daire_ozeti = next(
            (item for item in daire_aidat_ozetleri_listesi if item['daire'].id == kullanici_kendi_dairesi.id), None)
        if kendi_daire_ozeti:
            daire_aidat_ozetleri_listesi.remove(kendi_daire_ozeti)
            daire_aidat_ozetleri_listesi.insert(0, kendi_daire_ozeti)
    # 4. Madde (12 satır) için backend'de veri hazırlığı:
    # Örnek: Eğer aidat_listesi_ozet her zaman en az 12 elemanlı olsun isteniyorsa,
    # ve gerçek veri sayısı 12'den az ise, kalanını boş objelerle doldurabilirsiniz.
    # Bu, template'i basitleştirir.
    # min_rows = 12
    # if len(daire_aidat_ozetleri_listesi) < min_rows:
    #     for _ in range(min_rows - len(daire_aidat_ozetleri_listesi)):
    #         daire_aidat_ozetleri_listesi.append({
    #             'blok_daire': "-", 'sakin': "-", 'yillik_borc': Decimal('0.00'),
    #             'odenen': Decimal('0.00'), 'bakiye': Decimal('0.00'), 'is_current_user_flat': False,
    #             'daire': None, # Veya boş bir placeholder Daire objesi, detay linki için None kontrolü yapılmalı
    #             'is_placeholder': True # Template'de farklı stil için kullanılabilir
    #         })

    site_gider_listesi_data = Gider.objects.filter(
        site=site_obj).order_by('-tarih', '-id')[:50]
    daire_aidat_detay_listesi_data = Aidat.objects.filter(daire__blok__site=site_obj).select_related(
        'daire__blok', 'daire__kullanici').order_by('-tarih', '-id')[:50]

    context = {
        'site': site_obj, 'kullanici_kendi_dairesi': kullanici_kendi_dairesi, 'current_year': current_year,
        'toplam_gelir_aidatlar': toplam_gelir_aidatlar, 'toplam_giderler': toplam_giderler, 'kasa_bakiyesi': kasa_bakiyesi,
        'aidat_listesi_ozet': daire_aidat_ozetleri_listesi,
        'site_gider_listesi': site_gider_listesi_data,
        'daire_aidat_detay_listesi': daire_aidat_detay_listesi_data,
        # Hatalıysa hatalı form, değilse initial form
        'aidat_form_panel': aidat_form_instance,
        # Hatalıysa hatalı form, değilse initial form
        'gider_form_panel': gider_form_instance,
        'can_manage_site_finances': can_manage_site_finances,
        'kullanici_mevcut_bakiye_panel': kullanici_mevcut_bakiye_panel,
    }
    return render(request, 'yonetim/panel.html', context)

# --- GİRİŞ, KAYIT, ÇIKIŞ ve DİĞER VIEW'LERİNİZ OLDUĞU GİBİ KALABİLİR ---
# ... (giris, kayit, cikis, ajax_bloklar, ajax_daireler, site_bilgi, gider_update, gider_delete, aidat_update, aidat_delete, daire_odeme_detay fonksiyonları)
# Bu fonksiyonlar önceki halleriyle büyük ölçüde aynı kalabilir, paneldeki form gönderimiyle doğrudan ilgili değiller.
# Sadece `views.py` dosyasının tamamını vermeniz istendiği için buraya eklenecekler.
# Aşağıya diğer fonksiyonlarınızı ekleyebilirsiniz.
# Örnek olarak giris fonksiyonunu ekliyorum, diğerlerini de benzer şekilde ekleyebilirsiniz:


def giris(request):
    if request.user.is_authenticated:
        return redirect('yonetim:panel')

    form_data = request.POST.copy() if request.method == 'POST' else {}

    if request.method == 'POST':
        first_name = form_data.get('first_name', '').strip()
        last_name = form_data.get('last_name', '').strip()
        site_kodu_giris = form_data.get('site_kodu', '').strip().upper()
        blok_id_giris_str = form_data.get('blok_id_giris')
        daire_id_giris_str = form_data.get('daire_id_giris')
        password_giris = form_data.get('password')

        if not all([first_name, last_name, site_kodu_giris, blok_id_giris_str, daire_id_giris_str, password_giris]):
            messages.error(request, "Tüm alanların doldurulması zorunludur.")
        else:
            try:
                daire_obj = Daire.objects.select_related('kullanici', 'blok__site').get(
                    id=int(daire_id_giris_str),
                    blok_id=int(blok_id_giris_str),
                    blok__site__kod=site_kodu_giris,
                    kullanici__is_active=True,
                    kullanici__first_name__iexact=first_name,
                    kullanici__last_name__iexact=last_name
                )
                if daire_obj.kullanici:
                    user_to_auth = authenticate(
                        request, username=daire_obj.kullanici.username, password=password_giris)
                    if user_to_auth is not None:
                        if user_to_auth.site_kodu.upper() == site_kodu_giris:
                            login(request, user_to_auth)
                            return redirect('yonetim:panel')
                        else:
                            messages.error(
                                request, "Kullanıcı ve site bilgileri arasında uyuşmazlık var.")
                    else:
                        messages.error(
                            request, "Parola hatalı veya kullanıcı bulunamadı.")
                else:
                    messages.error(
                        request, "Belirtilen dairede aktif bir sakin kaydı bulunamadı.")
            except (Daire.DoesNotExist, ValueError):
                messages.error(request, "Giriş bilgileri hatalı veya eksik.")
            except Exception as e:
                messages.error(
                    request, f"Giriş sırasında bir hata oluştu: {e}")
                logger.error(f"Giriş hatası: {e}", exc_info=True)

        if site_kodu_giris:
            try:
                site_obj_for_options = Site.objects.get(kod=site_kodu_giris)
                form_data['blok_options_giris'] = list(Blok.objects.filter(
                    site=site_obj_for_options).order_by('ad').values('id', 'ad'))
                if blok_id_giris_str:
                    form_data['secili_blok_id_giris'] = blok_id_giris_str
                    blok_for_daire_options = Blok.objects.get(
                        id=int(blok_id_giris_str), site=site_obj_for_options)
                    daireler_q = Daire.objects.filter(
                        blok=blok_for_daire_options)
                    daireler_l_ajax = [{'id': d.id, 'no': str(
                        d.daire_no), 'sort_key': d.get_sortable_daire_no()} for d in daireler_q]
                    daireler_s_d_ajax = sorted(
                        daireler_l_ajax, key=lambda x: x['sort_key'])
                    form_data['daire_options_giris'] = [
                        {'id': d['id'], 'no': d['no']} for d in daireler_s_d_ajax]
                    if daire_id_giris_str:
                        form_data['secili_daire_id_giris'] = daire_id_giris_str
            except (Site.DoesNotExist, Blok.DoesNotExist, ValueError):
                form_data.pop('blok_options_giris', None)
                form_data.pop('daire_options_giris', None)
        return render(request, 'yonetim/giris.html', {'form_data': form_data})

    return render(request, 'yonetim/giris.html', {'form_data': {}})


@transaction.atomic
def kayit(request):
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
            messages.error(
                request, "Site kodu 3 karakterli ve sadece büyük harf ve rakamlardan oluşmalıdır.")
            return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        try:
            generated_username = generate_unique_username(
                first_name, last_name, site_kodu_form)
        except Exception as e:
            messages.error(request, str(e))
            return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        generated_email = f"{generated_username}@otomatikkayit.placeholder"
        is_yonetici_flag = (role == 'yonetici')
        site_nesnesi = None
        if is_yonetici_flag:
            if Site.objects.filter(kod=site_kodu_form).exists():
                messages.error(
                    request, f"'{site_kodu_form}' kodlu site mevcut.")
                return render(request, 'yonetim/kayit.html', {'form_data': form_data})
        else:
            if not sakin_blok_id or not sakin_daire_id:
                messages.error(
                    request, "Sakin için blok ve daire seçimi zorunludur.")
                if site_kodu_form:
                    try:
                        site_obj_for_options = Site.objects.get(
                            kod=site_kodu_form)
                        form_data['sakin_blok_options'] = list(Blok.objects.filter(
                            site=site_obj_for_options).order_by('ad').values('id', 'ad'))
                        if sakin_blok_id:
                            blok_for_daire_options = Blok.objects.get(
                                id=int(sakin_blok_id))
                            daireler_q = Daire.objects.filter(
                                blok=blok_for_daire_options, kullanici__isnull=True)
                            daireler_l_ajax = [{'id': d.id, 'no': str(
                                d.daire_no), 'sort_key': d.get_sortable_daire_no()} for d in daireler_q]
                            daireler_s_d_ajax = sorted(
                                daireler_l_ajax, key=lambda x: x['sort_key'])
                            form_data['sakin_daire_options'] = [
                                {'id': d['id'], 'no': d['no']} for d in daireler_s_d_ajax]
                    except (Site.DoesNotExist, Blok.DoesNotExist, ValueError):
                        pass
                return render(request, 'yonetim/kayit.html', {'form_data': form_data})
            try:
                site_nesnesi = Site.objects.get(kod=site_kodu_form)
            except Site.DoesNotExist:
                messages.error(
                    request, f"'{site_kodu_form}' kodlu site bulunamadı.")
                return render(request, 'yonetim/kayit.html', {'form_data': form_data})

        user = None
        try:
            user = Kullanici.objects.create_user(username=generated_username, email=generated_email, password=password,
                                                 first_name=first_name, last_name=last_name, site_kodu=site_kodu_form, is_yonetici=is_yonetici_flag)
            if is_yonetici_flag:
                login(request, user)
                messages.success(
                    request, f"Yönetici olarak kayıt oldunuz. '{site_kodu_form}' için site bilgilerinizi girin.")
                return redirect('yonetim:site_bilgi')
            else:
                daire_nesnesi = Daire.objects.get(
                    id=int(sakin_daire_id), blok_id=int(sakin_blok_id), blok__site=site_nesnesi)
                if daire_nesnesi.kullanici is not None:
                    user.delete()
                    messages.error(
                        request, f"{daire_nesnesi.daire_tam_adi} dolu.")
                    return render(request, 'yonetim/kayit.html', {'form_data': form_data})
                daire_nesnesi.kullanici = user
                daire_nesnesi.save()
                login(request, user)
                messages.success(
                    request, f"Sakin olarak kayıt oldunuz: {daire_nesnesi.daire_tam_adi}")
                return redirect('yonetim:panel')
        except Daire.DoesNotExist:
            if user:
                user.delete()
                messages.error(request, "Seçilen daire/blok geçersiz.")
        except IntegrityError:
            if user:
                user.delete()
                messages.error(request, "Kayıt hatası (IE).")
        except ValueError:
            if user:
                user.delete()
                messages.error(request, "Blok veya daire ID hatası.")
        except Exception as e:
            if user:
                user.delete()
            logger.error(f"Kayıt Genel Hata: {e}", exc_info=True)
            messages.error(request, f"Hata: {e}")
        form_data.pop('password', None)
        form_data.pop('password_confirm', None)
        return render(request, 'yonetim/kayit.html', {'form_data': form_data})
    else:
        site_kodu_get = request.GET.get('sitekodu', '').strip().upper()
        form_data = {'site_kodu': site_kodu_get}
        if site_kodu_get:
            form_data['rol'] = 'sakin'
            try:
                site_obj_for_options = Site.objects.get(kod=site_kodu_get)
                form_data['sakin_blok_options'] = list(Blok.objects.filter(
                    site=site_obj_for_options).order_by('ad').values('id', 'ad'))
            except Site.DoesNotExist:
                if site_kodu_get:
                    messages.warning(
                        request, f"'{site_kodu_get}' site kodu ile site bulunamadı.")
                form_data['site_kodu'] = ''
                form_data.pop('sakin_blok_options', None)
        else:
            form_data['rol'] = 'sakin'
    return render(request, 'yonetim/kayit.html', {'form_data': form_data})


@login_required
def cikis(request):
    logout(request)
    messages.info(request, "Başarıyla çıkış yaptınız.")
    return redirect('yonetim:giris')


def ajax_bloklar(request):
    site_kodu = request.GET.get('site_kodu', '').strip().upper()
    if not site_kodu or not re.match(r"^[A-Z0-9]{3}$", site_kodu):
        return JsonResponse({'error': 'Geçerli bir site kodu girin.'}, status=400)
    try:
        site_obj = Site.objects.get(kod=site_kodu)
        bloklar_data = list(Blok.objects.filter(
            site=site_obj).order_by('ad').values('id', 'ad'))
        if not bloklar_data:
            return JsonResponse({'bloklar': [], 'message': 'Bu sitede henüz blok tanımlanmamış.'})
        return JsonResponse({'bloklar': bloklar_data})
    except Site.DoesNotExist:
        return JsonResponse({'error': f"'{site_kodu}' kodlu site bulunamadı."}, status=404)
    except Exception as e:
        logger.error(f"AJAX Bloklar hatası: {e}", exc_info=True)
        return JsonResponse({'error': 'Sunucu hatası.'}, status=500)


def ajax_daireler(request):
    blok_id_str = request.GET.get('blok_id', None)
    list_all_str = request.GET.get('list_all', 'false').lower()

    if not blok_id_str:
        return JsonResponse({'error': 'Blok ID gerekli.'}, status=400)
    try:
        blok_id = int(blok_id_str)
        blok_obj = Blok.objects.get(id=blok_id)

        daireler_queryset = Daire.objects.filter(blok=blok_obj)
        if list_all_str != 'true':
            daireler_queryset = daireler_queryset.filter(
                kullanici__isnull=True)

        daireler_list_for_sort = [{'id': d.id, 'no': str(
            d.daire_no), 'sort_key': d.get_sortable_daire_no()} for d in daireler_queryset]
        daireler_sorted_list = sorted(
            daireler_list_for_sort, key=lambda x: x['sort_key'])
        daireler_data = [{'id': d['id'], 'no': d['no']}
                         for d in daireler_sorted_list]

        if not daireler_data:
            message = 'Bu blokta daire bulunmamaktadır.'
            if list_all_str != 'true':
                message = 'Bu blokta kayıt olunabilecek boş daire bulunmamaktadır.'
            return JsonResponse({'daireler': [], 'message': message})
        return JsonResponse({'daireler': daireler_data})
    except ValueError:
        return JsonResponse({'error': 'Geçersiz Blok ID.'}, status=400)
    except Blok.DoesNotExist:
        return JsonResponse({'error': 'Blok bulunamadı.'}, status=404)
    except Exception as e:
        logger.error(f"AJAX Daireler hatası: {e}", exc_info=True)
        return JsonResponse({'error': 'Sunucu hatası.'}, status=500)


@login_required
@transaction.atomic
def site_bilgi(request):
    is_read_only_mode = not request.user.is_yonetici
    site_obj = None
    try:
        site_obj = Site.objects.select_related(
            'yonetici').get(kod=request.user.site_kodu)
    except Site.DoesNotExist:
        if request.user.is_yonetici:
            pass
        else:
            messages.error(request, "İlişkili site bulunamadı.")
            return redirect('yonetim:panel')
    if site_obj is None and not request.user.is_yonetici:
        messages.error(request, "Site bilgileri görüntülenemiyor.")
        return redirect('yonetim:panel')
    if site_obj and not site_obj.yonetici and request.user.is_yonetici:
        site_obj.yonetici = request.user
        site_obj.save()

    yoneticinin_mevcut_dairesi = Daire.objects.filter(
        blok__site=site_obj, kullanici=request.user).first() if site_obj and request.user.is_yonetici else None
    form_data_initial = {}
    if site_obj:
        form_data_initial = {'ad': site_obj.ad, 'adres': site_obj.adres,
                             'yonetici_tel': site_obj.yonetici_tel, 'aidat_miktari': site_obj.aidat_miktari, 'kod': site_obj.kod}
    elif request.user.is_yonetici:
        form_data_initial['kod'] = request.user.site_kodu

    if request.method == 'POST':
        if is_read_only_mode:
            messages.error(
                request, "Bu sayfada değişiklik yapma yetkiniz yok.")
            return redirect('yonetim:site_bilgi')

        site_adi = request.POST.get('site_adi', '').strip()
        site_adresi = request.POST.get('site_adresi', '').strip()
        yonetici_tel = request.POST.get('yonetici_tel', '').strip()
        aidat_miktari_str = request.POST.get('aidat_miktari', '').strip()
        yonetici_daire_id_str = request.POST.get('yonetici_daire_secimi')

        blok_idler = request.POST.getlist('blok_id[]')
        blok_adlari = request.POST.getlist('blok_adi[]')
        daire_sayilari_str = request.POST.getlist('daire_sayisi[]')

        if not site_adi or not site_adresi:
            messages.error(request, "Site adı ve adresi zorunludur.")
        else:
            aidat_miktari_form = None
            if aidat_miktari_str:
                try:
                    aidat_miktari_form = Decimal(
                        aidat_miktari_str.replace(',', '.'))
                except InvalidOperation:
                    messages.error(request, "Geçersiz aidat miktarı formatı.")
                    aidat_miktari_form = None  # Hata durumunda None yap

            try:
                with transaction.atomic():
                    # Yeni site oluşturuluyor (sadece yönetici ilk kayıtta buraya gelir)
                    if site_obj is None:
                        site_obj = Site.objects.create(
                            ad=site_adi, adres=site_adresi, kod=request.user.site_kodu,
                            yonetici=request.user, yonetici_tel=yonetici_tel, aidat_miktari=aidat_miktari_form
                        )
                        messages.success(
                            request, f"'{site_obj.ad}' sitesi başarıyla oluşturuldu.")
                    else:  # Mevcut site güncelleniyor
                        site_obj.ad = site_adi
                        site_obj.adres = site_adresi
                        site_obj.yonetici_tel = yonetici_tel
                        site_obj.aidat_miktari = aidat_miktari_form
                        site_obj.save()
                        messages.success(
                            request, "Site bilgileri güncellendi.")

                    # Yönetici Daire Ataması
                    if request.user.is_yonetici:
                        mevcut_yonetici_dairesi = Daire.objects.filter(
                            blok__site=site_obj, kullanici=request.user).first()
                        if yonetici_daire_id_str == "bos_birak":
                            if mevcut_yonetici_dairesi:
                                mevcut_yonetici_dairesi.kullanici = None
                                mevcut_yonetici_dairesi.save()
                                messages.info(
                                    request, "Yöneticinin daire ataması kaldırıldı.")
                        elif yonetici_daire_id_str:
                            try:
                                secilen_daire = Daire.objects.get(
                                    id=int(yonetici_daire_id_str), blok__site=site_obj)
                                if secilen_daire.kullanici is None or secilen_daire.kullanici == request.user:
                                    if mevcut_yonetici_dairesi and mevcut_yonetici_dairesi != secilen_daire:
                                        mevcut_yonetici_dairesi.kullanici = None
                                        mevcut_yonetici_dairesi.save()
                                    secilen_daire.kullanici = request.user
                                    secilen_daire.save()
                                    messages.success(
                                        request, f"Yönetici {secilen_daire.daire_tam_adi} dairesine atandı.")
                                else:
                                    messages.error(
                                        request, f"Seçilen daire ({secilen_daire.daire_tam_adi}) başka bir sakine ait. Atama yapılamadı.")
                            except (Daire.DoesNotExist, ValueError):
                                messages.error(
                                    request, "Geçersiz yönetici daire seçimi.")

                    # Blok ve Daire İşlemleri
                    gelen_blok_idler_set = set()
                    for i in range(len(blok_adlari)):
                        blok_adi = blok_adlari[i].strip().upper()
                        daire_sayisi_str_item = daire_sayilari_str[i].strip()
                        blok_id_str_item = blok_idler[i]

                        if not blok_adi or not daire_sayisi_str_item:
                            messages.warning(
                                request, f"{i+1}. sıradaki blok adı veya daire sayısı boş bırakılamaz. Atlandı.")
                            continue
                        try:
                            daire_sayisi = int(daire_sayisi_str_item)
                        except ValueError:
                            messages.warning(
                                request, f"'{blok_adi}' için geçersiz daire sayısı. Atlandı.")
                            continue
                        if daire_sayisi <= 0:
                            messages.warning(
                                request, f"'{blok_adi}' için daire sayısı pozitif olmalı. Atlandı.")
                            continue

                        if blok_id_str_item:  # Mevcut blok güncelleniyor
                            try:
                                blok_nesnesi = Blok.objects.get(
                                    id=int(blok_id_str_item), site=site_obj)
                                blok_nesnesi.ad = blok_adi
                                blok_nesnesi.save()
                                gelen_blok_idler_set.add(blok_nesnesi.id)
                                # Daire sayısı değişikliği (azalma varsa kullanıcıları silme, artma varsa yeni daire ekleme)
                                mevcut_daire_sayisi = blok_nesnesi.daireler.count()
                                if daire_sayisi > mevcut_daire_sayisi:
                                    for j in range(mevcut_daire_sayisi + 1, daire_sayisi + 1):
                                        Daire.objects.create(
                                            blok=blok_nesnesi, daire_no=str(j))
                                elif daire_sayisi < mevcut_daire_sayisi:
                                    # Daire sayısı azaltılırken, sondaki boş dairelerden başlanarak silinir. Doluysa silinmez, uyarılır.
                                    silinecek_daire_sayisi = mevcut_daire_sayisi - daire_sayisi
                                    sondaki_daireler = sorted(
                                        list(blok_nesnesi.daireler.all()), key=daire_natural_sort_key, reverse=True)
                                    silinen_count = 0
                                    for daire_to_check_delete in sondaki_daireler:
                                        if silinen_count >= silinecek_daire_sayisi:
                                            break
                                        if daire_to_check_delete.kullanici is None:
                                            daire_to_check_delete.delete()
                                            silinen_count += 1
                                        else:
                                            messages.warning(
                                                request, f"{daire_to_check_delete.daire_tam_adi} dolu olduğu için silinemedi. Daire sayısı azaltma işlemi tam yapılamadı.")
                                            # Dolu daireye denk gelince dur.
                                            break
                            except Blok.DoesNotExist:
                                messages.error(
                                    request, "Güncellenmeye çalışılan blok bulunamadı.")
                                continue
                        else:  # Yeni blok ekleniyor
                            yeni_blok = Blok.objects.create(
                                site=site_obj, ad=blok_adi)
                            gelen_blok_idler_set.add(yeni_blok.id)
                            for j in range(1, daire_sayisi + 1):
                                Daire.objects.create(
                                    blok=yeni_blok, daire_no=str(j))

                    # Formdan gelmeyen, veritabanında olan blokları sil
                    db_blok_idler_set = set(Blok.objects.filter(
                        site=site_obj).values_list('id', flat=True))
                    silinecek_blok_idler = db_blok_idler_set - gelen_blok_idler_set
                    for blok_id_to_delete in silinecek_blok_idler:
                        try:
                            blok_to_delete = Blok.objects.get(
                                id=blok_id_to_delete)
                            if blok_to_delete.daireler.filter(kullanici__isnull=False).exists():
                                messages.warning(
                                    request, f"'{blok_to_delete.ad}' bloğunda sakinler olduğu için blok silinemedi.")
                            else:
                                blok_to_delete.delete()
                                messages.info(
                                    request, f"'{blok_to_delete.ad}' bloğu ve içindeki boş daireler silindi.")
                        except Blok.DoesNotExist:
                            pass  # Zaten yoksa sorun değil

                # Başarılı POST sonrası panele dön
                return redirect('yonetim:panel')
            except IntegrityError as e:
                messages.error(request, f"Veritabanı hatası: {e}")
            except Exception as e:
                messages.error(request, f"Genel bir hata oluştu: {e}")
            # Hata durumunda form_data_initial güncel kalmalı (sayfa yeniden render edilecek)
            form_data_initial = request.POST.copy()  # Hatalı formu doldurmak için
            # Blok ve daireleri de form_data'ya eklemek gerekebilir ama bu karmaşıklaşır.
            # Şimdilik sadece ana site bilgileri korunuyor. Bloklar için DB'den okunacak.

    # GET veya hatalı POST için context
    bloklar_ve_daireleri_c = []
    sitedeki_bos_ve_yoneticiye_ait_d = []
    # site_obj None değilse (yani ya DB'den geldi ya da yeni oluşturuldu ama hata oldu)
    if site_obj:
        for blok_db in site_obj.bloklar.all().order_by('ad'):
            bloklar_ve_daireleri_c.append(
                {'id': blok_db.id, 'ad': blok_db.ad, 'daire_sayisi': blok_db.daireler.count()})
        if request.user.is_yonetici:
            daire_q_for_yonetici_select = Daire.objects.filter(
                blok__site=site_obj).select_related('blok', 'kullanici')
            temp_d_list_for_yonetici_select = sorted(
                list(daire_q_for_yonetici_select), key=daire_natural_sort_key)
            for d_item in temp_d_list_for_yonetici_select:
                if d_item.kullanici is None or d_item.kullanici == request.user:
                    sitedeki_bos_ve_yoneticiye_ait_d.append(d_item)

    context = {
        'site': site_obj,
        'bloklar_ve_daireleri': bloklar_ve_daireleri_c,
        # Bu durum POST sonrası pek olmaz, GET'te önemli
        'is_yeni_site': site_obj is None and request.user.is_yonetici,
        'form_data': form_data_initial,  # GET'te DB'den, POST'ta hataysa request.POST'tan
        'is_read_only': is_read_only_mode,
        'sitedeki_bos_ve_yoneticiye_ait_daireler': sitedeki_bos_ve_yoneticiye_ait_d,
        'yoneticinin_mevcut_dairesi_id': yoneticinin_mevcut_dairesi.id if yoneticinin_mevcut_dairesi else None,
    }
    return render(request, 'yonetim/site_bilgi.html', context)


@login_required
def gider_update(request, gider_id):
    try:
        site_obj = Site.objects.get(kod=request.user.site_kodu)
    except Site.DoesNotExist:
        messages.error(request, "İlişkili site bulunamadı.")
        return redirect('yonetim:panel')
    if not request.user.is_yonetici or request.user != site_obj.yonetici:
        messages.error(request, "Yetkiniz yok.")
        return redirect('yonetim:panel')
    gider_obj = get_object_or_404(Gider, id=gider_id, site=site_obj)
    if request.method == 'POST':
        # Prefix yok çünkü bu form ayrı sayfada
        form = GiderForm(request.POST, request.FILES, instance=gider_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Gider güncellendi.")
            return redirect(reverse('yonetim:panel') + '#siteGiderListesiPane')
    else:
        form = GiderForm(instance=gider_obj)  # Prefix yok
    return render(request, 'yonetim/gider_form.html', {'form': form, 'gider': gider_obj, 'site': site_obj})


@login_required
def gider_delete(request, gider_id):
    try:
        site_obj = Site.objects.get(kod=request.user.site_kodu)
    except Site.DoesNotExist:
        messages.error(request, "İlişkili site bulunamadı.")
        return redirect('yonetim:panel')
    if not request.user.is_yonetici or request.user != site_obj.yonetici:
        messages.error(request, "Yetkiniz yok.")
        return redirect('yonetim:panel')
    gider_obj = get_object_or_404(Gider, id=gider_id, site=site_obj)
    if request.method == 'POST':
        gider_obj.delete()
        messages.success(request, "Gider silindi.")
        return redirect(reverse('yonetim:panel') + '#siteGiderListesiPane')
    # GET isteği için bir onay sayfası render edilebilir, örneğin gider_confirm_delete.html
    # Şimdilik direkt panele yönlendiriyoruz ama bu iyi bir pratik değil.
    # return render(request, 'yonetim/gider_confirm_delete.html', {'gider': gider_obj, 'site': site_obj})
    return redirect(reverse('yonetim:panel') + '#siteGiderListesiPane')


@login_required
def aidat_update(request, aidat_id):
    aidat_obj = get_object_or_404(Aidat, id=aidat_id)
    daire_obj = aidat_obj.daire
    site_obj_of_aidat = daire_obj.blok.site
    # Sadece site yöneticisi ve o siteye ait yönetici bu işlemi yapabilmeli.
    # VEYA aidatın sahibi olan kullanıcı (kendi aidatını düzenleyebilsin isteniyorsa) - Şimdilik sadece yönetici
    if not (request.user.is_yonetici and request.user.site_kodu == site_obj_of_aidat.kod):
        messages.error(request, "Bu aidat kaydını düzenleme yetkiniz yok.")
        return redirect('yonetim:panel')

    if request.method == 'POST':
        form = AidatForm(request.POST, request.FILES,
                         instance=aidat_obj)  # Prefix yok, ayrı sayfa
        if form.is_valid():
            form.save()
            messages.success(request, "Aidat kaydı güncellendi.")
            # from_panel parametresi ile gelmişse panele, yoksa daire detayına dön
            if 'from_panel' in request.GET:
                return redirect(reverse('yonetim:panel') + '#daireAidatDetayListesiPane')
            else:
                return redirect('yonetim:daire_odeme_detay', daire_id=daire_obj.id)
    else:
        form = AidatForm(instance=aidat_obj)  # Prefix yok

    context = {
        'form': form,
        'aidat': aidat_obj,
        'daire': daire_obj,
        'site': site_obj_of_aidat
    }
    return render(request, 'yonetim/aidat_form.html', context)


@login_required
def aidat_delete(request, aidat_id):
    aidat_obj = get_object_or_404(Aidat, id=aidat_id)
    daire_obj = aidat_obj.daire
    site_obj_of_aidat = daire_obj.blok.site
    if not (request.user.is_yonetici and request.user.site_kodu == site_obj_of_aidat.kod):
        messages.error(request, "Bu aidat kaydını silme yetkiniz yok.")
        return redirect('yonetim:panel')

    redirect_url = reverse('yonetim:panel') + '#daireAidatDetayListesiPane' if 'from_panel' in request.GET else reverse(
        'yonetim:daire_odeme_detay', kwargs={'daire_id': daire_obj.id})

    if request.method == 'POST':
        aidat_obj.delete()
        messages.success(request, "Aidat kaydı silindi.")
        return redirect(redirect_url)

    # GET isteği için bir onay sayfası (aidat_confirm_delete.html) olmalı
    context = {
        'aidat': aidat_obj,
        'daire': daire_obj,
        'site': site_obj_of_aidat,
        'cancel_url': redirect_url
    }
    # return render(request, 'yonetim/aidat_confirm_delete.html', context)
    # Şimdilik onay sayfası olmadan direkt siliyoruz (POST ile gelinmeli)
    # Eğer GET ile gelinirse bir şey yapma veya onay sayfasına yönlendir. Güvenlik için GET ile silme yapılmamalı.
    # Bu fonksiyon sadece POST ile çalışmalı ya da GET için onay göstermeli.
    # Mevcut haliyle direkt redirect yapıyor, bu POST ile gelinmediyse mantıksız.
    # Onay sayfası olmadığı için, sadece POST ise sil, değilse ana sayfaya dön diyelim.
    messages.warning(
        request, "Silme işlemi için POST isteği gereklidir veya onay sayfası kullanılmalıdır.")
    return redirect(redirect_url)


@login_required
def daire_odeme_detay(request, daire_id):
    try:
        daire_obj = get_object_or_404(Daire.objects.select_related(
            'blok__site', 'kullanici'), id=daire_id, blok__site__kod=request.user.site_kodu)
    except:
        messages.error(
            request, "Daire bulunamadı veya bu daireyi görüntüleme yetkiniz yok.")
        return redirect('yonetim:panel')

    site_obj = daire_obj.blok.site
    # Yetki kontrolü: Kullanıcı ya o dairenin sakini olmalı ya da sitenin yöneticisi
    is_site_yonetici = request.user.is_yonetici and request.user.site_kodu == site_obj.kod
    is_daire_sakini = daire_obj.kullanici == request.user

    if not (is_site_yonetici or is_daire_sakini):
        messages.error(
            request, "Bu dairenin ödeme detaylarını görüntüleme yetkiniz yok.")
        return redirect('yonetim:panel')

    aidatlar = Aidat.objects.filter(daire=daire_obj).order_by('-tarih', '-id')
    toplam_odenen_tum = aidatlar.aggregate(toplam=Sum('tutar'))[
        'toplam'] or Decimal('0.00')

    current_year_val = timezone.now().year  # Değişken adı düzeltildi
    yillik_borc = (site_obj.aidat_miktari *
                   12) if site_obj.aidat_miktari else Decimal('0.00')
    odenen_bu_yil = Aidat.objects.filter(daire=daire_obj, tarih__year=current_year_val).aggregate(
        toplam=Sum('tutar'))['toplam'] or Decimal('0.00')
    bakiye_bu_yil = odenen_bu_yil - yillik_borc

    context = {
        'daire_nesnesi': daire_obj,
        'aidatlar': aidatlar,
        'toplam_odenen_tum_zamanlar_daire': toplam_odenen_tum,
        'yillik_aidat_borcu_daire': yillik_borc,
        'odenen_bu_yil_daire': odenen_bu_yil,
        'bakiye_bu_yil_daire': bakiye_bu_yil,
        'current_year': current_year_val,  # Değişken adı context'e doğru aktarıldı
        'site': site_obj,
        # Sadece yönetici detay sayfasında düzenleme yapabilsin
        'can_edit_aidat_records_on_detail': is_site_yonetici,
    }
    return render(request, 'yonetim/daire_odeme_detay.html', context)
