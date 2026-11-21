# monit-docker 2.0.1

Requires Sonicprobe 0.3.66. Application email and queued SMTP logs sent through
Sonicprobe receive missing Date and Message-ID headers. Existing header values
are preserved. This prevents rejection caused by missing origin headers; it does
not guarantee final delivery by the receiving mail server.

Upgrade the installed package or rebuild the application image, then restart the
service so the running process loads the updated dependency.

## Français

Cette version exige Sonicprobe 0.3.66. Les courriels et journaux SMTP envoyés par
Sonicprobe reçoivent les en-têtes Date et Message-ID manquants, sans remplacer les
valeurs existantes. Mettre à jour le paquet ou reconstruire l’image, puis
redémarrer le service. La remise finale reste à vérifier auprès du relais SMTP.
