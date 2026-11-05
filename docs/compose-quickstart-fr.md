# Installer avec Docker Compose

[English version](compose-quickstart.md)

Trois installations sont proposées. Toutes démarrent sans règle de redémarrage
automatique des conteneurs surveillés.

| Besoin | Fichiers | Accès local |
| --- | --- | --- |
| Agent seul et API | `docker-compose.yml` | `http://127.0.0.1:9808` |
| Agent et interface web | Base et `docker-compose.ui.yml` | `https://localhost:8443` |
| Historique et graphiques | `examples/monitoring/compose.yaml` | Grafana sur le port 3000 |

## Socket Docker filtré en option

Avec Compose **2.24.4 ou ultérieur**, depuis la racine du dépôt :

```sh
export DOCKER_SOCKET_GID=$(stat -c %g "${DOCKER_SOCKET_PATH:-/var/run/docker.sock}")
docker compose -f docker-compose.yml -f docker-compose.socket-proxy.yml up -d --build --wait
```

Seul le proxy monte le socket de l’hôte. L’agent le joint sur un réseau privé et
le proxy ne publie aucun port hôte. Par défaut, l’agent peut lire la version,
la liste et les détails des conteneurs, leurs statistiques, les événements et
les détails d’une image par son empreinte. L’inspection peut révéler les
variables d’environnement et les montages. Gardez ce réseau privé.

Pour autoriser d’autres opérations, exportez la variable voulue, puis relancez
la commande Compose. Chaque valeur est une liste d’**identifiants complets**
séparés par des espaces. Obtenez un identifiant avec
`docker inspect -f '{{.Id}}' NOM`. Actualisez la liste après toute recréation
du conteneur. Pour révoquer les nouvelles requêtes, supprimez la variable avec
`unset` et réappliquez la configuration.

| Variable | Droits sur les conteneurs désignés |
| --- | --- |
| `MONIT_PROXY_EXEC_TARGETS` | Sondes de fichiers et commandes Docker exec sans le mode privilégié, y compris en tant que root dans le conteneur |
| `MONIT_PROXY_LIFECYCLE_TARGETS` | Démarrage, arrêt et redémarrage |

Choisissez uniquement des conteneurs de confiance. Une commande dans un
conteneur privilégié ou disposant du socket Docker ou du système de fichiers
hôte peut donner accès à l’hôte. Le proxy lui-même conserve le vrai socket.
Les règles de supervision et les jetons pour les actions manuelles se
configurent séparément. Utilisez les deux mêmes fichiers Compose pour `down`.

## Installation guidée

Sur Linux amd64, installer Git, Docker Engine, son plugin Compose v2 avec
`up --wait` et curl. Pour le tableau de bord, prévoir Python 3 et OpenSSL.

```sh
git clone --branch v1.2.0 --depth 1 https://github.com/decryptus/monit-docker.git
cd monit-docker
./install.sh
```

Appuyer sur Entrée pour ajouter le tableau de bord local. Au premier lancement,
choisir un identifiant et un mot de passe de 12 caractères minimum. Le script
vérifie la collecte et la demande d’authentification HTTPS, puis affiche le nombre
de conteneurs et l’adresse. Le certificat local autosigné est valable sept jours.
Après une connexion SSH, suivre l’exemple de tunnel affiché depuis son poste.
Relancer `./install.sh --ui` dans le même dossier réutilise les identifiants et le
volume existants. Pour l’API seule, utiliser `./install.sh --agent-only`.
Pour retirer une interface déjà installée, suivre la procédure d’arrêt ci-dessous.

## Préparer la machine

Utiliser Docker Engine sous Linux, son socket Unix local et une version récente
du plugin Compose avec `up --wait`. Le compte doit pouvoir accéder à Docker. Les
images publiées ciblent Linux amd64 ; une autre architecture demande une compilation
ou une émulation vérifiée séparément. Docker Desktop doit utiliser les conteneurs
Linux. Sous Windows, exécuter les commandes dans WSL avec l’intégration Docker.
La préparation de l’UI demande aussi Python 3 et OpenSSL.

```sh
git clone https://github.com/decryptus/monit-docker.git
cd monit-docker
docker compose version
docker info
```

Pour Docker rootless, définir `DOCKER_SOCKET_PATH` avec le chemin absolu du socket
local. Les contextes Docker distants ne sont pas couverts par cette procédure.

## Lancer seulement l’agent

```sh
docker compose config --quiet
docker compose up -d --wait --wait-timeout 180
curl -f http://127.0.0.1:9808/readyz
curl -f http://127.0.0.1:9808/v1/status
```

La collecte tourne toutes les 30 secondes après la fin du cycle précédent.
`readyz` renvoie HTTP 200 lorsque les mesures sont fraîches et la collecte réussie.
En cas d’erreur, consulter `docker compose ps` et
`docker compose logs --tail=100 monit-docker`.

L’API reste accessible uniquement en local. L’agent reçoit seulement le socket
Docker, mais ce socket permet de contrôler Docker même si le montage est marqué
`read_only`. L’observation seule vient de l’absence de règles, pas du montage.

## Ajouter l’interface web

```sh
python3 examples/ui/prepare.py --self-signed
docker compose -f docker-compose.yml -f docker-compose.ui.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.ui.yml up -d --wait --wait-timeout 180
```

Saisir un identifiant et un mot de passe d’au moins 12 caractères. Ouvrir
**https://localhost:8443**. Le certificat local est autosigné et valable sept
jours ; le navigateur signale qu’il n’est pas reconnu. Pour un vrai déploiement,
utiliser un certificat de confiance selon le [guide UI](ui.md).

La préparation se fait une seule fois et refuse d’écraser les secrets existants.
Réutiliser `examples/ui/secrets.local` aux démarrages suivants. L’UI utilise le
même agent, sans Prometheus ni Grafana, et n’active aucun bouton de contrôle des
conteneurs. L’API de l’agent reste accessible sur la boucle locale.

Conserver la même liste de fichiers pour arrêter cette installation :

```sh
docker compose -f docker-compose.yml -f docker-compose.ui.yml down
```

Cette commande conserve le volume d’état et les secrets. `--volumes` supprimerait
les données du volume : ne pas l’ajouter pour un arrêt normal. Pour revenir à
l’agent seul, arrêter avec les deux fichiers, puis relancer le fichier de base.
Les surcharges avancées d’actions et de journal du dossier `examples/ui` ont leur
propre procédure ; ne pas les mélanger avec ces fichiers racine.

## Régler les ports et mettre à jour

Définir `MONIT_DOCKER_PORT`, `UI_PORT` ou `DOCKER_SOCKET_PATH` dans le shell avant
Compose si nécessaire. `UI_BIND` vaut `127.0.0.1` par défaut. Garder le même nom
de projet et le même répertoire pour réutiliser le volume.

Les images de l’agent et de l’UI sont fixées à **1.2.0**. Pour une évolution,
choisir des versions correspondantes, préserver la configuration et les données,
puis utiliser `pull` et `up -d --wait` avec la même liste de fichiers. Un simple
`pull` ne remplace pas les conteneurs en cours d’exécution.

## Remplacer l’ancien exemple cron

L’ancien fichier racine utilisait `latest`, montait tout `/var/run` et exécutait
des règles cron, dont un signal PHP-FPM. Le nouveau lance uniquement `serve` en
observation. **Les anciens jobs `MONIT_DOCKER_CRONS` ne seront plus exécutés.**

Avant la mise à jour, sauvegarder hors du dépôt l’ancien Compose, la configuration,
les états et les journaux. Arrêter l’ancienne installation avec ce fichier et son
nom de projet d’origine. Pour conserver des jobs, reprendre explicitement leurs
cibles et règles selon le [guide cron](cron.md), avec un état persistant et une
simulation préalable. Aucune conversion automatique des règles n’est effectuée.

À partir de la prochaine version après 1.2.0, la commande `monit-docker serve`
peut aussi lire ses réglages dans `monit-docker.yml`. Exemple :

```yaml
serve:
  port: 9810
  interval: 60
  rsc: [status, health]
  status_token_file: /run/secrets/status
```

Une option explicite en ligne de commande est prioritaire. Avec cet exemple,
`serve --port 9808 --rsc pid` écoute sur 9808, collecte uniquement le PID
et conserve l’intervalle et le chemin du jeton définis dans le fichier.
Les listes `rsc` et `cmd` sont remplacées en entier. Pour les noms de clés,
remplacer les tirets des options longues par des traits de soulignement.
La simulation ponctuelle utilise toujours `--dry-run`.
Pour désactiver temporairement un booléen activé dans `serve:`, utilisez
`--no-allow-actions`, `--no-allow-maintenance` ou `--no-trust-proxy-user`.

Pour protéger la lecture de `/v1/status`, montez un fichier secret dans
l’agent et ajoutez `--status-token-file /run/secrets/status` après `serve`.
Le fichier contient 64 caractères hexadécimaux minuscules. Utilisez un secret
distinct pour chaque usage. Les clients envoient le jeton dans l’en-tête Bearer
de la requête. Si vous utilisez le tableau de bord, configurez son proxy pour
ajouter cet en-tête uniquement sur la route de statut. Voir le [guide de
l’agent](serve.md).

## Continuer avec une démonstration

Le [tutoriel mémoire](tutorial-memory.md) ajoute un conteneur jetable et permet
d’activer explicitement un seul redémarrage automatique. La limite mémoire du
conteneur et la sélection exacte évitent de prendre un service existant comme cible.

La [pile Prometheus et Grafana](compose.md) reste une installation distincte pour
l’historique. Arrêter la pile minimale ou modifier ses ports avant de la lancer.
Les notifications demandent une [configuration supplémentaire](notifications.md).

## Réglages de cron, stats et monit

La prochaine version après 1.2.0 permet de placer les valeurs habituelles dans
trois sections de `monit-docker.yml`. Exemple pour une lecture de métriques :

```yaml
stats:
  rsc: [cpu_percent, mem_usage]
  output: json
monit:
  rsc: [status]
```

`monit-docker stats --output text` utilise les ressources du fichier et affiche
du texte. Avec `monit --rsc status`, seules les mesures demandées sont lues,
même si le YAML définit une commande. Pour programmer une règle, renseignez
`cron.cmd` et `cron.state_file`, puis testez avec
`monit-docker --name web cron --dry-run`. Le [guide cron](cron.md) contient
un exemple complet.
