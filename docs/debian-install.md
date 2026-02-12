# Déploiement de Snippet Vault sur Debian 13

Ces instructions expliquent comment installer et exécuter l'application Snippet Vault (service FastAPI) sur une instance Debian 13 « Trixie ». Adaptez les chemins ou valeurs selon votre contexte.

## Prérequis

- Accès `sudo` sur la machine Debian
- Connexion réseau sortante (pour `apt`, `pip` et Git)
- Nom de domaine facultatif si vous prévoyez un reverse proxy HTTPS

## 1. Mise à jour du système

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y python3 python3-pip python3-venv sqlite3 git
```

`python3-venv` permet de créer un environnement isolé, `sqlite3` facilite les vérifications ponctuelles, et Git récupère le code source.

## 2. Récupération du code (sous l'utilisateur `jit`)

Les instructions supposent que votre utilisateur normal se nomme `jit` et que l'application se trouve dans `~/daw1_app`.

```bash
mkdir -p ~/daw1_app
cd ~/daw1_app
git clone https://example.com/votre-fork.git .
```

Adaptez l'URL Git à votre source (GitHub, GitLab, etc.). Si le dépôt est déjà présent, mettez-le simplement à jour (`git pull`).

## 3. Environnement Python et dépendances

```bash
mkdir -p ~/.venvs
python3 -m venv ~/.venvs/snippetvault
~/.venvs/snippetvault/bin/pip install --upgrade pip
~/.venvs/snippetvault/bin/pip install -e "$HOME/daw1_app[dev]"
```

L'installation en mode editable simplifie les mises à jour ultérieures depuis Git.

## 4. Configuration de l'application

Générez un secret de session unique, puis créez un fichier d'environnement lisible par `systemd` :

```bash
SECRET=$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(40))
PY
)
sudo tee /etc/snippetvault.env >/dev/null <<EOF
SIMPPETSSRV_DB_URL=sqlite+pysqlite:////home/jit/daw1_app/data/snippets.db
SIMPPETSSRV_SESSION_SECRET=$SECRET
SIMPPETSSRV_ADMIN_EMAIL=admin@example.com
SIMPPETSSRV_ADMIN_PASSWORD=ChangezMoi123!
SIMPPETSSRV_HOST=127.0.0.1
SIMPPETSSRV_PORT=8000
EOF
unset SECRET
```

Ensuite, préparez le répertoire de données :

```bash
mkdir -p ~/daw1_app/data
chmod 750 ~/daw1_app/data
```

> Remplacez l'email, le mot de passe et, si besoin, le secret de session par vos valeurs. Le secret ne doit pas contenir d'espaces.

## 5. Lancement manuel (vérification)

```bash
cd ~/daw1_app
source ~/.venvs/snippetvault/bin/activate
set -a
source /etc/snippetvault.env
set +a
python -m simppetssrv
```

Accédez ensuite à `http://127.0.0.1:8000/healthz` depuis la machine (ou via tunnel SSH) pour vérifier que le serveur répond `{"status": "ok"}`. Si tout fonctionne, interrompez le processus (`Ctrl+C`).

## 6. Service systemd

Créez une unité `systemd` pour un démarrage automatique sous l'utilisateur `jit` :

```bash
sudo tee /etc/systemd/system/snippetvault.service >/dev/null <<'EOF'
[Unit]
Description=Snippet Vault FastAPI service
After=network.target

[Service]
Type=simple
EnvironmentFile=/etc/snippetvault.env
User=jit
Group=jit
WorkingDirectory=/home/jit/daw1_app
ExecStart=/home/jit/.venvs/snippetvault/bin/python -m simppetssrv
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Activez le service :

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now snippetvault.service
sudo systemctl status snippetvault.service
```

`systemctl status` doit afficher l'état `active (running)`. En cas d'erreur, consultez `journalctl -u snippetvault.service`.

## 7. Reverse proxy Nginx (optionnel mais recommandé)

Installez Nginx et configurez-le pour exposer l'application en HTTPS :

```bash
sudo apt install -y nginx
sudo tee /etc/nginx/sites-available/snippetvault >/dev/null <<'EOF'
server {
    listen 80;
    server_name snippets.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/snippetvault /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Ajoutez ensuite un certificat TLS (via `certbot --nginx` ou tout autre outil). Pensez à définir `SIMPPETSSRV_FORCE_HTTPS=true` dans `/etc/snippetvault.env` lorsque vous forcez les redirections HTTPS.

## 8. Pare-feu (UFW optionnel)

```bash
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 9. Mises à jour et maintenance

Pour mettre à jour l'application :

```bash
sudo systemctl stop snippetvault.service
cd ~/daw1_app
git pull
~/.venvs/snippetvault/bin/pip install -e "$HOME/daw1_app[dev]"
sudo systemctl start snippetvault.service
```

Automatisez les sauvegardes en copiant régulièrement `~/daw1_app/data/snippets.db`. Testez aussi `pytest` après les mises à jour si vous modifiez le code.

---

Vous disposez maintenant d'un service Snippet Vault opérationnel sur Debian 13, prêt à gérer vos extraits de code.
