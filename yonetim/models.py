from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.db.models import Sum # Toplam hesaplaması için eklendi

# Kullanıcı modeli / User model
class Kullanici(AbstractUser):
    site_kodu = models.CharField(max_length=8, verbose_name=_("Site Kodu / Site Code"))
    is_yonetici = models.BooleanField(default=False, verbose_name=_("Yönetici mi? / Is Manager?"))
    # Kullanıcının ilişkili olduğu daireyi doğrudan burada tutmak yerine
    # Daire modelindeki ForeignKey üzerinden erişmek daha doğru.
    # eger bir kullanici birden fazla daireye sahip olabilecekse Daire.kullanici ManyToMany olmali.
    # Su anki yapiya gore bir dairenin bir kullanicisi olabilir (ForeignKey Daire'de)
    # veya bir kullanici birden fazla daireye atanabilir (ForeignKey Daire'de, related_name='daireler' ile).

    def __str__(self):
        return self.get_full_name() or self.username

# Site modeli / Site model
class Site(models.Model):
    ad = models.CharField(max_length=100, verbose_name=_("Site Adı / Site Name"))
    adres = models.CharField(max_length=255, verbose_name=_("Adres / Address"))
    kod = models.CharField(max_length=8, unique=True, verbose_name=_("Site Kodu / Site Code"))
    yonetici = models.ForeignKey(Kullanici, on_delete=models.CASCADE, verbose_name=_("Yönetici / Manager"))
    yonetici_tel = models.CharField(max_length=20, blank=True, null=True, verbose_name=_("Yönetici Telefon / Manager Phone"))
    aidat_miktari = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name=_("Aylık Aidat Miktarı"))
    # Aidatların hangi tarihten itibaren hesaplanacağını belirtmek için bir başlangıç tarihi eklenebilir.
    # aidat_baslangic_tarihi = models.DateField(null=True, blank=True, verbose_name=_("Aidat Başlangıç Tarihi"))


    def __str__(self):
        return self.ad

# Blok modeli / Block model
class Blok(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='bloklar')
    ad = models.CharField(max_length=50, verbose_name=_("Blok Adı / Block Name"))

    def __str__(self):
        return f"{self.site.ad} - {self.ad.upper()}" # Gösterimde de büyük harf kullanalım

    def save(self, *args, **kwargs):
        self.ad = self.ad.upper() # Kaydetmeden önce blok adını büyük harfe çevir
        super().save(*args, **kwargs) # Orijinal save metodunu çağır

    class Meta:
        ordering = ['ad']
        # İki blok adı aynı site içinde benzersiz olmalı mı? Öyleyse:
        # unique_together = ('site', 'ad')

# Daire modeli / Flat model
class Daire(models.Model):
    blok = models.ForeignKey(Blok, on_delete=models.CASCADE, related_name='daireler')
    daire_no = models.CharField(max_length=10, verbose_name=_("Daire No / Flat Number")) # Örn: "1", "2A", "15"
    kullanici = models.ForeignKey(Kullanici, on_delete=models.SET_NULL, null=True, blank=True, related_name='daireler', verbose_name=_("Sakin / Resident"))
    telefon_no = models.CharField(max_length=20, blank=True, verbose_name=_("Telefon No / Phone Number"))
    # borc_durumu gibi alanlar dinamik olarak hesaplanacağı için modele eklenmesine gerek yok.

    def __str__(self):
        return f"{self.blok.ad} - Daire {self.daire_no}"

    @property
    def daire_tam_adi(self):
        return f"{self.blok.ad} - {self.daire_no}"

    # Daire no'yu sayısal ve metinsel kısımlarına ayırarak sıralama için anahtar üretir
    def get_sortable_daire_no(self):
        import re
        parts = re.findall(r'(\d+)|([A-Za-z]+)', str(self.daire_no)) # str() eklendi, daire_no her zaman string olmayabilir
        processed_parts = []
        for num, char_group in parts:
            if num:
                processed_parts.append(int(num))
            elif char_group:
                processed_parts.append(char_group.lower())
        if not processed_parts and self.daire_no:
            try: return [int(self.daire_no)]
            except ValueError: return [str(self.daire_no).lower()] # str() eklendi
        return processed_parts if processed_parts else [str(self.daire_no).lower()] # str() eklendi


    class Meta:
        # Varsayılan sıralama yerine view'da özel sıralama yapılacak
        # ordering = ['blok__ad', 'daire_no'] # Bu, "1", "10", "2" şeklinde sıralar.
        pass

# Aidat modeli / Dues model
class Aidat(models.Model):
    daire = models.ForeignKey(Daire, on_delete=models.CASCADE, related_name='aidatlar')
    # tutar alanı, site.aidat_miktari'ndan alınacaksa veya formda gizli bir alandan gelecekse bu şekilde kalabilir.
    # Eğer tamamen makbuzdan okunacak ve hiçbir yerde belirtilmeyecekse bu alanın varlığı sorgulanabilir veya null=True, blank=True yapılabilir.
    # Şimdilik, panel view'da site.aidat_miktari'ndan alınacağını varsayıyoruz.
    tutar = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Tutar / Amount"))
    tarih = models.DateField(verbose_name=_("Tarih / Date"))
    aciklama = models.CharField(max_length=255, blank=True, verbose_name=_("Açıklama / Description"))
    makbuz = models.FileField(upload_to='aidat_makbuzlari/', null=True, blank=True, verbose_name=_("Makbuz / Receipt"))

    def __str__(self):
        return f"{self.daire} - {self.tutar} TL ({self.tarih})"

    class Meta:
        ordering = ['-tarih', '-id'] # En son aidatlar üstte

# Gider modeli / Expense model
class Gider(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='giderler')
    tur = models.CharField(max_length=50, verbose_name=_("Gider Türü / Expense Type"))
    tutar = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Tutar / Amount"))
    tarih = models.DateField(verbose_name=_("Tarih / Date"))
    aciklama = models.CharField(max_length=255, blank=True, verbose_name=_("Açıklama / Description"))
    makbuz = models.FileField(upload_to='gider_makbuzlari/', null=True, blank=True, verbose_name=_("Makbuz / Receipt"))

    def __str__(self):
        return f"{self.tur} - {self.tutar} TL ({self.tarih})"

    class Meta:
        ordering = ['-tarih', '-id'] # En son giderler üstte