# Nova 24/7 en un VPS

Guía para dejar Nova corriendo sin parar en un servidor (Ubuntu/Debian), sin
depender de tu PC ni de ngrok.

## Idea general

```
   Twilio (voz) ─┐
                 ├──►  https://tu-dominio.com  ──►  nginx (HTTPS)  ──►  127.0.0.1:5000
   Telegram ─────┘                                       (proxy)         Nova (Flask)
                                                                            │
                                                                        systemd lo
                                                                        mantiene vivo
```

- **nginx** recibe el tráfico público y lo pasa al Flask interno. Además pone el
  **HTTPS** (Twilio y Telegram lo exigen).
- **systemd** arranca Nova al encender el VPS y lo reinicia solo si se cae.
- **`WEBHOOK_BASE_URL`** del `.env` le dice a Nova su dirección pública. Con eso
  ya **no hace falta ngrok**.

## Lo que necesitas

| Requisito | Nota |
|---|---|
| VPS Ubuntu 22.04/24.04 (o Debian) | El plan más pequeño alcanza |
| Un **dominio o subdominio** | Ej: `nova.tudominio.com`. Necesario para el HTTPS |
| Puertos **80 y 443 abiertos** | En el firewall del proveedor (y `ufw`) |
| Las credenciales | Twilio (3) + Telegram (2) en el `.env` |

> Un subdominio es suficiente y gratis. Crea un registro **A** que apunte a la
> IP del VPS y espera unos minutos a que propague.

---

## Paso 1 — Sube el proyecto al VPS

Desde **tu PC** (PowerShell), copia la carpeta al servidor:

```powershell
scp -r "C:\Users\AMD\Desktop\llamadas\nova-bot-llamadas-main\nova-bot-llamadas-main" root@IP_DEL_VPS:/opt/nova
```

O, si usas Git, clona el repositorio en el VPS en `/opt/nova`.

Luego entra por SSH:

```bash
ssh root@IP_DEL_VPS
cd /opt/nova
```

## Paso 2 — Prepara el `.env`

En el VPS, edita `/opt/nova/.env` con tus datos reales:

```ini
NOMBRE_EMPRESA=Universidad Telecom

TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=tu_auth_token_real
TWILIO_NUMBER=+12025550123

TELEGRAM_BOT_TOKEN=1234567890:AAEabcDEF...
TELEGRAM_GROUP_CHAT_ID=-5565766651

PUERTO=5000

# Aqui va tu dominio (paso 3). De momento dejalo y vuelve luego.
WEBHOOK_BASE_URL=https://nova.tudominio.com
```

Y apunta tu `numeros.txt` (o hazlo luego con `/agregar`):

```
+573001234567
```

## Paso 3 — Apunta el dominio (DNS)

En tu proveedor de dominio crea un registro **A**:

| Tipo | Nombre | Valor |
|---|---|---|
| A | `nova` | `IP_DEL_VPS` |

Espera a que propague (segundos a minutos). Verifica desde el VPS:

```bash
getent hosts nova.tudominio.com
```

Debe devolver la IP del VPS. Ya puedes continuar.

## Paso 4 — Instala todo con un comando

El script instala Python, nginx, certbot, crea el usuario `nova`, el entorno
virtual, y activa el servicio systemd:

```bash
cd /opt/nova
sudo bash desplegar/instalar.sh
```

Al terminar, Nova ya está corriendo. Comprueba:

```bash
systemctl status nova
journalctl -u nova -f     # Ctrl+C para salir
```

Debe aparecer algo como:

```
Nova escuchando en http://127.0.0.1:5000  (túnel: https://nova.tudominio.com)
Números a marcar: ['+573001234567']
```

> Si dice **`Faltan credenciales en .env`**, revisa el Paso 2.

## Paso 5 — Publica el HTTPS

```bash
sudo cp desplegar/nginx-nova.conf /etc/nginx/sites-available/nova
sudo ln -s /etc/nginx/sites-available/nova /etc/nginx/sites-enabled/nova
sudo nano /etc/nginx/sites-available/nova     # cambia TU_DOMINIO por el tuyo
sudo nginx -t && sudo systemctl reload nginx

# Certificado de Let's Encrypt (rellena tu dominio y tu correo):
sudo certbot --nginx -d nova.tudominio.com --agree-tos -m tu@correo.com --redirect
```

Certbot edita el archivo solo y añade el bloque de **443** con redirección de
HTTP a HTTPS. Comprueba desde fuera (o desde el VPS):

```bash
curl https://nova.tudominio.com/salud
```

Debe responder `{"ok": true, ...}`.

## Paso 6 — Reinicia con la URL pública y registra el webhook

```bash
sudo systemctl restart nova

# Registra el webhook de Telegram (una sola vez; repetir si cambia el dominio):
sudo -u nova .venv/bin/python registrar_webhook.py
```

Debe decir **`Webhook registrado correctamente`**.

> En el VPS **no arranques ngrok**. Como `WEBHOOK_BASE_URL` está en el `.env`,
> el webhook se registra contra tu dominio real.

## Paso 7 — Prueba en Telegram

En el grupo **#1 Transfer > Nova**:

1. `/estado` → responde el estado. ✅ Todo conectado.
2. `/agregar +573001234567` (si no lo pusiste en `numeros.txt`).
3. `/llamar` → sale un menú: elige a cuál número llamar. 📞

---

## ¿Y si reinicia Twilio o cambia algo?

| Situación | Qué hacer |
|---|---|
| Reinicio del VPS | Nada: systemd levanta Nova solo |
| Nova se cae | Nada: systemd lo reinicia en 5 s |
| Cambias el dominio | `WEBHOOK_BASE_URL` en `.env` → `systemctl restart nova` → re-correr `registrar_webhook.py` |
| Cambias credenciales | Edita `.env` → `sudo systemctl restart nova` |
| No llega nada a Telegram | Mira `journalctl -u nova -f` y verifica el webhook con `registrar_webhook.py --info` |

## Comandos útiles

```bash
sudo systemctl status nova      # ¿está vivo?
sudo systemctl restart nova     # reiniciar
sudo systemctl stop nova        # detener
sudo systemctl start nova       # arrancar
journalctl -u nova -f           # ver registros en vivo
journalctl -u nova --since "1 hour ago"   # registros de la última hora
```

## Actualizar el código

Cuando cambies algo en tu PC, sube los archivos y reinicia:

```bash
# desde tu PC
scp servidor.py config.py root@IP_DEL_VPS:/opt/nova/

# en el VPS
sudo systemctl restart nova
```

> Sube también los `.py` nuevos si añadiste rutas o comandos.

## Notas de seguridad

- El `.env` **no** se sube a Git (ya está en `.gitignore`). En el VPS déjalo en
  `/opt/nova/.env` con permisos del usuario `nova`.
- Twilio cobra por llamada y por minuto. `/apagar` detiene el ciclo, pero no
  corta las llamadas ya marcadas.
- Si el grupo de Telegram no es privado, cualquiera podría usar `/agregar` o
  `/llamar`. Mantenlo restringido.