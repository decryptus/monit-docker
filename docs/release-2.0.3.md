# Monit-Docker 2.0.3

`GET /v1/status` now includes `agent_version`, read from the running package.
`api_version` remains 1. The field is available before the first successful
collection and during degraded monitoring; it does not change access control.

The overview displays that version and identifies the UI build version in its
tooltip. UI metadata is embedded from the release version when the image is
built. A mixed agent/UI installation reports both identities accurately.
Older agents without the field and disconnected agents do not leave a stale
version badge. The existing FR/EN language choice applies to version labels.

Use the matching 2.0.3 agent and UI images. Preserve configuration, authentication
and audit/state volumes. No database migration or account change is required.
A custom reverse proxy banner should identify the demo without a version number;
it must not substitute a handwritten version for the API value.

## Français

`GET /v1/status` fournit désormais `agent_version`, lu dans le paquet exécuté.
`api_version` reste à 1. Ce champ est disponible au démarrage et lorsque la
collecte est dégradée, sans changement des règles d’accès.

Le tableau de bord affiche cette version. L’infobulle indique aussi la version
de l’interface intégrée à sa construction. Deux versions différentes restent
identifiables lors d’une mise à jour partielle. Le numéro est masqué lorsque
l’agent ne le fournit pas ou devient injoignable.

Utilisez les images agent/interface 2.0.3 en conservant la configuration, les
comptes et les volumes existants. Aucune migration de base ni modification de
compte n’est nécessaire. Un bandeau de démo personnalisé doit rester sans numéro
écrit dans Nginx : la version affichée vient désormais de l’application.
