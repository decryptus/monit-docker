# Monit-Docker 2.0.2

New accounts created through `auth user-add` or the administration API require
an email identifier. Existing legacy accounts, sessions, roles, password resets
and explicit htpasswd imports remain compatible. No automatic rename occurs.

Use an unquoted ASCII mailbox and a dotted DNS domain, at most 254 characters.
Identifiers retain their exact case. Web sign-in accepts the longer identifiers;
mobile clients must also support them. Syntax validation does not establish
mailbox ownership and does not enable automatic email password recovery.

At server startup, `auth_accounts_without_email` reports the number of legacy
accounts once, including disabled accounts, without printing account names.
Monitoring and login remain available. Use the matching agent/UI 2.0.2 images;
back up existing configuration and persistent state before updating.

## Français

Les nouveaux comptes CLI/API exigent un identifiant e-mail. Les anciens comptes
restent utilisables sans changement d’identifiant, de droits ou de sessions.
Le formulaire web accepte jusqu’à 254 caractères ; l’application mobile doit
également prendre en charge ce format. La casse exacte est conservée.

Un avertissement au démarrage indique le nombre de comptes sans identifiant
e-mail, sans exposer leur nom ni bloquer l’utilisation. Cela ne vérifie pas la
boîte de réception et n’active pas la récupération automatique par e-mail.
Sauvegardez la configuration et les données avant la mise à jour des images
agent/interface correspondantes en 2.0.2.
