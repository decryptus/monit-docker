# Installer avec Docker Compose

[English version](compose-quickstart.md)

Trois installations sont proposées. Toutes démarrent sans règle de redémarrage
automatique des conteneurs surveillés.

| Besoin | Fichiers | Accès local |
| --- | --- | --- |
| Agent seul et API | `docker-compose.yml` | `http://127.0.0.1:9808` |
| Agent et interface web | Base et `docker-compose.ui.yml` | `https://localhost:8443` |
| Historique et graphiques | `examples/monitoring/compose.yaml` | Grafana sur le port 3000 |

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

Les images de l’agent et de l’UI sont fixées à **1.1.1**. Pour une évolution,
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

## Continuer avec une démonstration

Le [tutoriel mémoire](tutorial-memory.md) ajoute un conteneur jetable et permet
d’activer explicitement un seul redémarrage automatique. La limite mémoire du
conteneur et la sélection exacte évitent de prendre un service existant comme cible.

La [pile Prometheus et Grafana](compose.md) reste une installation distincte pour
l’historique. Arrêter la pile minimale ou modifier ses ports avant de la lancer.
Les notifications demandent une [configuration supplémentaire](notifications.md).
