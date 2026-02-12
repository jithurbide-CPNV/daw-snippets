# Installation sécurisée de simppetssrv (Snippet Vault) sur Debian 13

Ce guide décrit une installation durcie du service FastAPI `simppetssrv` sur Debian 13 (Trixie), en prévision d’un reverse proxy Nginx futur. Pour l’instant, le service s’exécute directement via Uvicorn, mais la configuration isole l’application et prépare l’intégration HTTPS.

## 1. Préparer le système

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y python3 python3-venv python3-pip sqlite3 git
```

`python3-venv` permet l’isolement des dépendances, `sqlite3` simplifie les contrôles sur la base locale, Git récupère le code.

## 2. Créer un compte applicatif dédié

Pour limiter l’impact d’un éventuel incident, l’application tourne sous un utilisateur non privilégié et un répertoire dédié :

```bash
sudo adduser --system --group --home /opt/daw-snippets simppet
```

Ce compte n’a pas de shell interactif, mais `sudo -u simppet` permettra d’exécuter les commandes de maintenance.

## 3. Récupérer le code depuis GitHub

```bash
sudo -u simppet -H mkdir -p /opt
sudo -u simppet -H git clone https://github.com/jithurbide-CPNV/daw-snippets.git /opt/daw-snippets
```

Si le dossier existe déjà (par exemple lors d’une mise à jour), remplacez la commande `git clone` par :

```bash
sudo -u simppet -H bash -c 'cd /opt/daw-snippets && git pull'
```

Si vous utilisez un fork privé, adaptez l’URL Git en conséquence.

## 4. Installer les dépendances dans un virtualenv

```bash
sudo -u simppet -H python3 -m venv /opt/daw-snippets/.venv
sudo -u simppet -H /opt/daw-snippets/.venv/bin/pip install --upgrade pip
sudo -u simppet -H /opt/daw-snippets/.venv/bin/pip install -e "/opt/daw-snippets[dev]"
```

Le mode editable simplifie la maintenance (pas besoin de réinstaller après un `git pull`).

## 5. Créer le fichier d’environnement

Le service lira ses variables dans `/etc/simppetssrv.env`. Cette étape se déroule en trois sous-étapes :

1. **Générer un secret de session aléatoire** (utilisé pour signer les cookies).
2. **Écrire toutes les variables dans `/etc/simppetssrv.env`**.
3. **Préparer le répertoire de la base SQLite**.

Exécute chaque commande ci-dessous dans l’ordre :

```bash
# 1) Générer un secret unique et le stocker dans une variable shell temporaire
SECRET=$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(40))
PY
)

# 2) Créer /etc/simppetssrv.env avec les informations de connexion et d’admin
cat <<EOF | sudo tee /etc/simppetssrv.env >/dev/null
SIMPPETSSRV_DB_URL=sqlite+pysqlite:////opt/daw-snippets/data/snippets.db
SIMPPETSSRV_SESSION_SECRET=$SECRET
SIMPPETSSRV_ADMIN_EMAIL=admin@example.com
SIMPPETSSRV_ADMIN_PASSWORD=ChangezMoi123!
SIMPPETSSRV_HOST=0.0.0.0
SIMPPETSSRV_PORT=8000
# laissé à false tant que le reverse proxy HTTPS n’est pas en place
SIMPPETSSRV_FORCE_HTTPS=false
EOF

# 3) Ne plus conserver le secret en clair dans la session actuelle
unset SECRET

# 4) Créer le dossier qui contiendra la base SQLite et lui appliquer les bons droits
sudo -u simppet -H mkdir -p /opt/daw-snippets/data
sudo chmod 750 /opt/daw-snippets/data
sudo chown simppet:simppet /opt/daw-snippets/data
```

Adaptez l’email et le mot de passe administrateur, ainsi que le secret si vous préférez le définir vous-même. Le fichier `/etc/simppetssrv.env` est volontairement placé hors du répertoire applicatif et n’est lisible que par `root`; `systemd` le fournira au service au démarrage. Aucune configuration SMTP n’est nécessaire : les comptes sont actifs dès leur création. La valeur `SIMPPETSSRV_HOST=0.0.0.0` permet d’écouter sur toutes les interfaces – assurez-vous que votre pare-feu filtre correctement l’accès externe.

## 6. Vérifier le lancement manuel (facultatif)

```bash
sudo -u simppet -H bash -c '
  cd /opt/daw-snippets
  source .venv/bin/activate
  set -a
  source /etc/simppetssrv.env
  set +a
  python -m simppetssrv
'
```

Ouvrez `http://<IP_DE_LA_VM>:8000/healthz` depuis un poste client (ou `curl` depuis la VM). Une fois validé, interrompez l’application (`Ctrl+C`).

## 7. Créer le service systemd

```bash
sudo tee /etc/systemd/system/simppetssrv.service >/dev/null <<'EOF'
[Unit]
Description=Snippet Vault (simppetssrv) FastAPI service
After=network.target

[Service]
Type=simple
EnvironmentFile=/etc/simppetssrv.env
User=simppet
Group=simppet
WorkingDirectory=/opt/daw-snippets
ExecStart=/opt/daw-snippets/.venv/bin/python -m simppetssrv
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now simppetssrv.service
sudo systemctl status simppetssrv.service
```

Le statut doit afficher `active (running)`. En cas d’échec, consultez les journaux :

```bash
journalctl -u simppetssrv.service -xe
```

À ce stade, l’application répond sur `http://<IP_DE_LA_VM>:8000/`. Restreignez l’accès via pare-feu si vous ne souhaitez exposer le service qu’aux machines du labo.

## 8. Mises à jour et maintenance

```bash
sudo systemctl stop simppetssrv.service
sudo -u simppet -H bash -c '
  cd /opt/daw-snippets
  git pull
  /opt/daw-snippets/.venv/bin/pip install -e "/opt/daw-snippets[dev]"
'
sudo systemctl start simppetssrv.service
```

- Sauvegardez régulièrement `/opt/daw-snippets/data/snippets.db`.
- Après des changements importants, exécutez les tests automatisés : `sudo -u simppet -H /opt/daw-snippets/.venv/bin/pytest`.

---

L’application `simppetssrv` est maintenant installée proprement sur Debian 13, protégée par un utilisateur dédié et prête pour l’ajout futur d’un reverse proxy HTTPS.
