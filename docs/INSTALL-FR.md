# Aura — Installation (Mac mini 2012 ou n'importe quel PC)

Objectif : la machine démarre directement sur Aura en HDMI. Tu branches un disque dur, ses films et ses séries
apparaissent tout seuls. Tout se pilote depuis un téléphone, un ordinateur de la maison, ou directement à l'écran
avec une souris et un clavier. Temps total : ~40 minutes, dont 25 d'attente.
Le guide détaille le Mac mini 2012 ; sur un PC classique, les étapes sont les mêmes (touche `F12`/`F2`/`Suppr`
au lieu de `Alt` pour démarrer sur la clé USB).

## Ce qu'il te faut

- Mac mini 2012 (Macmini6,1 ou 6,2) + câble HDMI vers la TV
- Une clé USB de 2 Go ou plus (elle sera effacée)
- **Un câble Ethernet** branché au Mac mini pendant l'installation (le Wi-Fi Broadcom n'est pas
  disponible pendant l'installation Debian, il l'est après)
- Un clavier USB (uniquement pendant l'installation)
- Un PC pour préparer la clé

## Étape 1 — Préparer la clé USB (sur ton PC)

1. Télécharge l'image **Debian 13 netinst amd64** : <https://www.debian.org/download>
   (fichier `debian-13.x.x-amd64-netinst.iso`).
2. Écris-la sur la clé avec [Rufus](https://rufus.ie) (Windows) ou [balenaEtcher](https://etcher.balena.io).
   Dans Rufus : schéma de partition **GPT**, système cible **UEFI (non CSM)**, mode d'écriture **DD** si demandé.

## Étape 2 — Démarrer le Mac mini sur la clé

1. Branche : HDMI, Ethernet, clavier USB, la clé USB.
2. Allume le Mac mini en **maintenant la touche `Alt` (Option)** enfoncée jusqu'au menu de démarrage.
3. Choisis **« EFI Boot »** (la clé).
4. Dans le menu Debian, choisis **Install** (pas Graphical, plus rapide).

## Étape 3 — Installation Debian minimale (10 min)

Réponds ainsi (tout le reste : valeur par défaut, `Entrée`) :

| Question | Réponse |
|---|---|
| Langue / pays / clavier | Français / France / Français |
| Nom de machine | `tv` |
| Domaine | vide |
| Mot de passe root | **laisse vide** (le compte utilisateur aura sudo) |
| Utilisateur | `admin` et un mot de passe que tu retiens |
| Partitionnement | « Assisté – utiliser un disque entier », « Tout dans une seule partition » |
| Miroir | France, `deb.debian.org` |
| Firmware non libre | **Oui** |
| Sélection des logiciels | **décoche tout** sauf « utilitaires usuels du système » et **coche « serveur SSH »** |
| GRUB | Oui, sur le disque principal |

Retire la clé quand il le demande, le Mac redémarre sur Debian (écran texte noir avec `tv login:`).

> Installation entièrement automatique : le fichier `os/preseed/aura.cfg` répond à toutes ces
> questions. Au menu de démarrage de la clé, appuie sur `Tab` sur « Install » et ajoute
> `auto=true priority=critical url=http://<ip-de-ton-pc>:8000/aura.cfg` (sers le fichier avec
> `python -m http.server 8000` sur ton PC). Il lance aussi l'étape 4 tout seul au premier démarrage.

## Étape 4 — Installer Aura (1 commande, 15 min)

Connecte-toi avec `admin`, puis récupère le projet depuis GitHub. Le dépôt `Milis0f/aura` est privé : `git`
demande ton identifiant GitHub et, en guise de mot de passe, un jeton d'accès (GitHub › Settings › Developer settings ›
Personal access tokens, droit « Contents: read » sur ce dépôt).

```bash
sudo apt-get install -y git && git clone https://github.com/Milis0f/aura.git /tmp/aura && sudo AURA_SRC=/tmp/aura bash /tmp/aura/os/install.sh
```

Si le dépôt devient public, une seule commande suffit :

```bash
sudo apt-get install -y curl && curl -fsSL https://raw.githubusercontent.com/Milis0f/aura/main/os/install.sh | sudo bash
```

Sans dépôt GitHub (copie locale du projet sur une clé USB montée dans `/mnt/usb`) :

```bash
sudo AURA_SRC=/mnt/usb/aura bash /mnt/usb/aura/os/install.sh
```

Le script installe tout : pilotes Wi-Fi Broadcom (b43), accélération vidéo Intel HD 4000,
son HDMI (PipeWire), Google Chrome (Widevine pour Netflix/Prime/Canal+), mpv, le compositeur
kiosque `cage`, NetworkManager, mDNS (`aura.local`), la gestion des disques (udisks2, NTFS, exFAT),
ffmpeg pour lire la durée des vidéos, les services systemd, la mise à jour automatique nocturne,
le démarrage silencieux. Puis :

```bash
sudo reboot
```

> **NAS Dashboard déjà installé sur la machine ?** Le script le remplace : Aura reprend le port 8000 (un nginx
> déjà configuré continue de marcher), les réglages de `/etc/nasdash.env` et les comptes, avec les mêmes mots de
> passe et la même double authentification. Les fichiers de NAS Dashboard restent en place.

## Étape 5 — Configuration (5 min)

Deux façons, au choix : depuis le téléphone (recommandé) ou directement à l'écran avec une souris/clavier
(bouton « Configurer à l'écran » sur l'écran de bienvenue, ou Réglages › Assistant).

1. Au démarrage, la TV affiche **« Bienvenue sur Aura »** avec une adresse et un **QR code**.
2. Sur le téléphone (même Wi-Fi/box), scanne le QR : il ouvre la télécommande
   (`http://aura.local:8000/remote/`, ou l'IP affichée).
3. L'assistant :
   1. **Réseau** — si tu veux passer en Wi-Fi, choisis le réseau et entre le mot de passe. Tu peux ensuite débrancher l'Ethernet.
   2. **Source IPTV** — onglet *Xtream* : colle l'URL, l'identifiant et le mot de passe fournis par ton
      fournisseur. Ou *M3U* (lien), ou *Gratuit* (chaînes publiques iptv-org). Seulement des disques durs ? *Passer*.
   3. **Guide TV** — ajoute « EPGShare France ». Les sources Xtream ajoutent souvent leur EPG toutes seules.
   4. **Terminer.** La TV bascule sur l'accueil.
4. Optionnel mais conseillé : *Réglages › Métadonnées* → clé TMDB gratuite (2 minutes sur themoviedb.org).
   Sans clé, Aura utilise les infos du fournisseur puis des sources sans clé (iTunes, Wikipédia, TVMaze).

## Étape 6 — Ton compte (2 min)

Sur un ordinateur ou le téléphone, ouvre `http://aura.local:8000/`. La première visite crée le **compte du
propriétaire** (12 caractères minimum, une phrase courte marche très bien). Ensuite, dans l'appli :
*Réglages › Sécurité › Double authentification* (recommandé).

- À la maison, la TV, la télécommande et la bibliothèque marchent **sans compte** : les invités peuvent regarder.
- Le compte sert aux **fichiers**, aux **téléchargements** et à l'**accès depuis l'extérieur**.
- D'autres comptes, en SSH : `sudo aura-manage add prenom` (lecture seule par défaut ; il propose l'écriture,
  les téléchargements et la double authentification). Aussi : `list`, `passwd`, `perms`, `delete`, `audit`.

## Films et séries sur un disque dur

- **Branche le disque en USB.** La TV affiche « Disque « Nom » branché · recherche des films… ». Quelques secondes
  à quelques minutes plus tard, les titres sont dans *Bibliothèque* (TV, téléphone, appli web), avec affiche et
  résumé dès que le boîtier est en ligne.
- **Formats de disque** : NTFS et exFAT (disques Windows et Mac récents), FAT32, ext4, HFS+. Un disque débranché
  sans éjection depuis Windows est monté en lecture seule : la lecture marche, l'écriture non.
- **Rangement conseillé** (Aura comprend aussi les noms de fichiers « release ») :

  ```
  Films/Le Grand Bleu (1988).mkv
  Films/Inception (2010)/Inception.2010.1080p.mkv
  Séries/Dark/Saison 1/01 - Secrets.mkv
  Séries/Breaking.Bad.S02E05.720p.mkv
  ```

  Une image `poster.jpg` (ou `folder.jpg`, `cover.jpg`, `affiche.jpg`) dans le dossier d'un film sert d'affiche.
  Les vidéos de moins de 25 Mo (extraits, bandes-annonces) et les téléchargements en cours sont ignorés.
- **Lecture** : sur la TV, *Lire*, *Reprendre à…* ou un épisode précis. mpv lit le fichier directement depuis le
  disque avec la carte graphique (MKV, HEVC, plusieurs pistes audio et sous-titres). Pendant la lecture, au clavier
  ou avec le pavé du téléphone : **OK** pause, **◀ ▶** reculer 15 s / avancer 30 s, **A** piste audio,
  **S** sous-titres, **Retour** arrêter. L'épisode suivant démarre tout seul (désactivable dans l'appli web).
- **Depuis le téléphone** : *Parcourir › Bibliothèque*, touche un titre, *Lire sur la TV*.
- **Débrancher** : appli web › *Disques* › *Éjecter* (le disque s'arrête proprement). Débranché sans éjecter, ses
  titres quittent la liste mais la reprise est gardée : rebranche-le, tout revient.
- **Un film en ligne** (lien http/https vers une vidéo) : *Bibliothèque › Ajouter un lien*, sur le téléphone ou
  dans l'appli web.
- **Un dossier du boîtier** plutôt qu'un disque : appli web › *Disques* › *Suivre* un dossier.

## Fichiers et téléchargements

- Appli web › **Fichiers** : parcourir, chercher, envoyer (bouton ou glisser-déposer), renommer, déplacer,
  supprimer. Les disques branchés apparaissent tout seuls, les dossiers permanents se déclarent dans
  `/etc/aura.env` (`AURA_FILE_ROOTS=Media:/srv/disque/Media`), puis `sudo systemctl restart aura`.
- **Téléchargements** : Aura pilote qBittorrent s'il tourne sur la machine. Renseigne `AURA_QB_URL`,
  `AURA_QB_USER` et `AURA_QB_PASS` dans `/etc/aura.env`. Un téléchargement terminé est analysé tout seul et
  rejoint la bibliothèque.

## Accès depuis l'extérieur (optionnel)

- Le plus simple : **Tailscale** sur le boîtier et sur le téléphone. Aura considère Tailscale comme la maison.
- Avec un nom de domaine : `os/nginx-aura.conf` + certbot pour le HTTPS. Depuis Internet, seule la page de
  connexion répond sans compte, et les tentatives de connexion sont limitées.
- N'ouvre jamais directement le port 8000 sur ta box.

## Fluidité du direct

- Avant chaque lecture, Aura teste toutes les sources de la chaîne en parallèle et garde la plus rapide :
  un lien mort ne coûte plus 30 secondes d'écran noir.
- Sur un Mac mini ou un vieux PC, *Réglages › Lecture › Moteur vidéo* : « Automatique » ou « mpv ». Les flux
  `.ts` sont alors décodés par la carte graphique au lieu du processeur.
- Les chaînes marquées Ⓨ dans les listes publiques (directs YouTube, par exemple France 24) sont lues dans Aura.
- *Réglages › Lecture › Tester une chaîne* affiche, pour chaque source, si elle répond, en combien de temps
  et si elle passe en direct ou par le boîtier.

## Utilisation

- **TV** (`/tv/`, affichée par le boîtier) : Accueil, Bibliothèque, Direct, Sports, F1, UFC, Films, Séries, Apps,
  Favoris, Recherche, Réglages.
- **Téléphone** (`/remote/`, sans compte à la maison) : onglet *Télécommande* (D-pad, OK, retour, volume,
  play/pause, trackpad, clavier), *Parcourir* (Bibliothèque, Direct, Sports, Films, Séries, Favoris, Apps,
  Recherche : toucher = lecture sur la TV), *Réglages*. Ajoute la page à l'écran d'accueil (« Ajouter à l'écran
  d'accueil ») : elle se comporte comme une app.
- **Appli web** (`/`, avec compte) : bibliothèque (lecture sur la TV ou sur l'appareil), disques, fichiers,
  téléchargements, état du système, sécurité du compte, couleur d'accent (appliquée aussi à la TV).
- **Clavier / souris USB** sur le Mac mini : flèches, Entrée, Échap/Retour, `F` = favori, `I` = infos.
  En lecture : haut/bas = changer de chaîne, gauche/droite = avancer/reculer (vidéos).
- **Apps (Netflix, Prime, Canal+, Disney+, YouTube, DAZN, F1 TV…)** : elles s'ouvrent dans le navigateur
  du boîtier. Sur le téléphone, passe en mode *Trackpad* pour cliquer et *Clavier* pour taper
  l'identifiant. La connexion reste mémorisée. Limite Widevine sous Linux : 720p (parfois 1080p), pas de 4K.
- **Sports** : calendrier automatique pour la F1 (Jolpica), l'UFC (ufc.com), le football, le basket, la NFL,
  la NHL et le rugby (TheSportsDB). Ouvrir un événement propose les chaînes de tes sources qui le diffusent,
  classées par probabilité (guide TV + nom de chaîne).
- Si un flux tombe en cours de lecture, Aura réessaie une fois puis bascule aussitôt sur la source suivante
  de la même chaîne, dans la même langue.

## Si quelque chose ne va pas

| Symptôme | Que faire |
|---|---|
| Le disque branché n'apparaît pas | Appli web › *Disques* : s'il est listé « non monté », bouton *Monter*. Sinon en SSH : `lsblk -f` (le disque est-il vu ?) et `journalctl -u aura -n 50 \| grep -i automount`. |
| Des films manquent | Vidéos de moins de 25 Mo ignorées. *Disques › Analyser* relance l'analyse. Un nom illisible ? Range le fichier dans `Films/Titre (Année)/`. |
| Pas d'affiche | Le boîtier doit être en ligne. Avec une clé TMDB (*Réglages › Métadonnées*), les affiches sont meilleures. Sinon, dépose un `poster.jpg` dans le dossier du film. |
| Le direct saccade ou met longtemps à démarrer | *Réglages › Lecture* : moteur « mpv », puis *Tester une chaîne*. Si toutes les sources sont lentes, c'est la source IPTV elle-même. En Wi-Fi, préfère l'Ethernet. |
| Pas de Wi-Fi dans la liste | `sudo dmesg \| grep -i b43` ; vérifier que `firmware-b43-installer` est installé : `sudo apt-get install --reinstall firmware-b43-installer`, puis `sudo reboot`. |
| Pas de son HDMI | Réglages › Système (téléphone) › *Sortie audio* → choisir HDMI. Sinon `pactl list short sinks` en SSH. |
| Écran noir après le logo | `ssh admin@aura.local` puis `journalctl -u aura-kiosk -n 50`. Souvent un souci de GPU : vérifier `vainfo`. |
| La page `aura.local` ne répond pas | Utilise l'IP affichée sur la TV (Réglages sur la TV la montre aussi), port **8000**. Sur Android, mDNS peut ne pas marcher : IP obligatoire. |
| Mot de passe du compte oublié | En SSH : `sudo aura-manage passwd prenom`. |
| Téléchargements vides | qBittorrent doit tourner et `AURA_QB_URL` / `AURA_QB_USER` / `AURA_QB_PASS` être justes dans `/etc/aura.env`, puis `sudo systemctl restart aura`. |
| Netflix dit « navigateur non compatible » | Chrome est requis (pas Chromium). `google-chrome --version` doit répondre. |
| Mettre à jour Aura | Réglages › Système › *Mettre à jour* (ou c'est fait chaque nuit à 4h30). |
| Tout réinitialiser | `sudo systemctl stop aura && sudo rm -rf /var/lib/aura/* && sudo systemctl start aura` (efface aussi les comptes, les sources et la reprise de lecture ; les fichiers des disques ne sont pas touchés). |

## Hotspot de configuration (bonus)

Si le boîtier n'a aucun réseau au démarrage, il tente d'ouvrir un Wi-Fi **« Aura-Setup »**
(mot de passe `aura123`). Connecte le téléphone dessus et ouvre `http://10.42.0.1:8000/remote/`.
Sur le Mac mini 2012 le pilote `b43` ne supporte pas toujours le mode point d'accès : dans ce cas,
branche l'Ethernet ou saisis le Wi-Fi à l'écran avec un clavier USB (Réglages › *Configurer le Wi-Fi à l'écran*).

## Télécommande TV (CEC)

Le HDMI des Mac mini n'a pas de CEC : la télécommande de la TV ne peut pas piloter le boîtier
nativement. Options : téléphone (recommandé), mini-clavier sans fil USB (~15 €), ou adaptateur
Pulse-Eight USB-CEC (~40 €, support à venir).
