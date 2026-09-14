# Aura — Installation (Mac mini 2012 ou n'importe quel PC)

Objectif : la machine démarre directement sur Aura en HDMI, et tout se configure depuis un
téléphone ou directement à l'écran avec une souris et un clavier. Temps total : ~40 minutes, dont 25 d'attente.
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
> `python -m http.server` sur ton PC). Il lance aussi l'étape 4 tout seul au premier démarrage.

## Étape 4 — Installer Aura (1 commande, 15 min)

Connecte-toi avec `admin`, puis :

```bash
sudo apt-get install -y curl && curl -fsSL https://raw.githubusercontent.com/matteo-pollo/aura/main/os/install.sh | sudo bash
```

Sans dépôt GitHub (copie locale du projet sur une clé USB montée dans `/mnt/usb`) :

```bash
sudo AURA_SRC=/mnt/usb/aura bash /mnt/usb/aura/os/install.sh
```

Le script installe tout : pilotes Wi-Fi Broadcom (b43), accélération vidéo Intel HD 4000,
son HDMI (PipeWire), Google Chrome (Widevine pour Netflix/Prime/Canal+), mpv, le compositeur
kiosque `cage`, NetworkManager, mDNS (`aura.local`), les services systemd, la mise à jour
automatique nocturne, le démarrage silencieux. Puis :

```bash
sudo reboot
```

## Étape 5 — Configuration (5 min)

Deux façons, au choix : depuis le téléphone (recommandé) ou directement à l'écran avec une souris/clavier
(bouton « Configurer à l'écran » sur l'écran de bienvenue, ou Réglages › Assistant).

1. Au démarrage, la TV affiche **« Bienvenue sur Aura »** avec une adresse et un **QR code**.
2. Sur le téléphone (même Wi-Fi/box), scanne le QR ou ouvre `http://aura.local:8080/` (ou l'IP affichée).
3. L'assistant :
   1. **Réseau** — si tu veux passer en Wi-Fi, choisis le réseau et entre le mot de passe. Tu peux ensuite débrancher l'Ethernet.
   2. **Source IPTV** — onglet *Xtream* : colle l'URL, l'identifiant et le mot de passe fournis par ton
      fournisseur. Ou *M3U* (lien), ou *Gratuit* (chaînes publiques iptv-org).
   3. **Guide TV** — ajoute « EPGShare France ». Les sources Xtream ajoutent souvent leur EPG toutes seules.
   4. **Terminer.** La TV bascule sur l'accueil.
4. Optionnel mais conseillé : *Réglages › Métadonnées* → clé TMDB gratuite (2 minutes sur themoviedb.org).
   Sans clé, Aura utilise les infos du fournisseur puis des sources sans clé (Wikipédia, TVMaze).

### Films et séries

- Avec un compte **Xtream**, l'onglet Films/Séries se remplit tout seul : rangées par genre, ajouts récents,
  mieux notés, reprise de lecture, fiches avec affiche, résumé, casting, bande-annonce, saisons et épisodes.
- Sans abonnement : *Réglages › Sources › Gratuit › « Films classiques libres de droits »* ajoute ~300 films
  du domaine public (Internet Archive), lisibles directement.

### Fluidité du direct

- Avant chaque lecture, Aura teste toutes les sources de la chaîne en parallèle et garde la plus rapide :
  un lien mort ne coûte plus 30 secondes d'écran noir.
- Sur un Mac mini ou un vieux PC, *Réglages › Lecture › Moteur vidéo* : « Automatique » ou « mpv ». Les flux
  `.ts` sont alors décodés par la carte graphique au lieu du processeur.
- Les chaînes marquées Ⓨ dans les listes publiques (directs YouTube, par exemple France 24) sont lues dans Aura.
- *Réglages › Lecture › Tester une chaîne* affiche, pour chaque source, si elle répond, en combien de temps
  et si elle passe en direct ou par le boîtier.

Ajoute la page à l'écran d'accueil du téléphone (« Ajouter à l'écran d'accueil ») : elle se comporte comme une app.

## Utilisation

- **Téléphone** : onglet *Télécommande* (D-pad, OK, retour, volume, play/pause), *Parcourir*
  (Direct, Sports, F1, UFC, Films, Séries, Favoris, Apps, Recherche : toucher = lecture sur la TV).
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
| Le direct saccade ou met longtemps à démarrer | *Réglages › Lecture* : moteur « mpv », puis *Tester une chaîne*. Si toutes les sources sont lentes, c'est la source IPTV elle-même. En Wi-Fi, préfère l'Ethernet. |
| Pas de Wi-Fi dans la liste | `sudo dmesg \| grep -i b43` ; vérifier que `firmware-b43-installer` est installé : `sudo apt-get install --reinstall firmware-b43-installer`, puis `sudo reboot`. |
| Pas de son HDMI | Réglages › Système (téléphone) › *Sortie audio* → choisir HDMI. Sinon `pactl list short sinks` en SSH. |
| Écran noir après le logo | `ssh admin@aura.local` puis `journalctl -u aura-kiosk -n 50`. Souvent un souci de GPU : vérifier `vainfo`. |
| La page `aura.local` ne répond pas | Utilise l'IP affichée sur la TV (Réglages sur la TV la montre aussi). Sur Android, mDNS peut ne pas marcher : IP obligatoire. |
| Netflix dit « navigateur non compatible » | Chrome est requis (pas Chromium). `google-chrome --version` doit répondre. |
| Mettre à jour Aura | Réglages › Système › *Mettre à jour* (ou c'est fait chaque nuit à 4h30). |
| Tout réinitialiser | `sudo rm -rf /var/lib/aura/* && sudo systemctl restart aura` |

## Hotspot de configuration (bonus)

Si le boîtier n'a aucun réseau au démarrage, il tente d'ouvrir un Wi-Fi **« Aura-Setup »**
(mot de passe `aura123`). Connecte le téléphone dessus et ouvre `http://10.42.0.1:8080/`.
Sur le Mac mini 2012 le pilote `b43` ne supporte pas toujours le mode point d'accès : dans ce cas,
branche l'Ethernet ou saisis le Wi-Fi à l'écran avec un clavier USB (Réglages › *Configurer le Wi-Fi à l'écran*).

## Télécommande TV (CEC)

Le HDMI des Mac mini n'a pas de CEC : la télécommande de la TV ne peut pas piloter le boîtier
nativement. Options : téléphone (recommandé), mini-clavier sans fil USB (~15 €), ou adaptateur
Pulse-Eight USB-CEC (~40 €, support à venir).
