# Nova 24/7 con ngrok (plan gratuito)

Guía para dejar Nova expuesta a Internet con **ngrok gratis**, sin VPS y sin
pagar nada. Twilio y Telegram exigen **HTTPS**, y ngrok lo da solo.

> Esto **no** reemplaza a tu PC: el servidor de Nova sigue corriendo en tu
> máquina. Lo que se vuelve estable es la **URL pública** (dominio fijo).

## Idea general

```
   Twilio (voz) ─┐
                 ├──►  https://riptide-slingshot-mandarin.ngrok-free.dev  ──►  127.0.0.1:5000
   Telegram ─────┘            (ngrok, HTTPS gratis)                              Nova (Flask)
```

ngrok crea un túnel desde su nube hasta tu `localhost:5000`. Como el plan
gratuito te da **un dominio fijo** (`riptide-slingshot-mandarin.ngrok-free.dev`,
ya configurado), la URL **ya no cambia** al reiniciar: registras el webhook
**una sola vez** y listo.

---
 
## ✅ Ya está montado (tu cuenta)
 
| Dato | Valor |
|---|---|
| Dev domain (fijo) | `https://riptide-slingshot-mandarin.ngrok-free.dev` |
| Authtoken | ya configurado en `ngrok config check` |
| `WEBHOOK_BASE_URL` | ya fijada en el `.env` |
| Config real de ngrok | `...\Packages\ngrok.ngrok_1g87z0zv29zzc\LocalCache\Local\ngrok\ngrok.yml` (instalación MSIX) |
 
Solo falta el paso 3 (arrancar y registrar). Lo de abajo explica cómo se hizo,
por si algún día cambias de PC o reinstalas.

## Paso 0 — Crea la cuenta y copia tu "dev domain"

1. Entra a **https://dashboard.ngrok.com/signup** y crea la cuenta (no pide
   tarjeta).
2. En **Getting Started → Your Authtoken**, copia tu token.
3. En **Domains** (o **Universal Gateway → Domains**) verás tu **dev domain**
   (en este caso `riptide-slingshot-mandarin.ngrok-free.dev`). **Cópialo**.
4. Conecta ngrok a tu cuenta una vez (en tu PC ya está hecho):

   ```powershell
   ngrok config add-authtoken TU_AUTHTOKEN
   ```

## Paso 1 — Fija el dominio (así no cambia nunca)

**Opción A — un comando (la más rápida):**

```powershell
ngrok http --url=https://riptide-slingshot-mandarin.ngrok-free.dev 5000
```

> Verificado: esa URL se mantiene idéntica en cada reinicio, así que el webhook
> se registra **una sola vez**. En versiones antiguas el flag es `--domain` en
> lugar de `--url`.

**Opción B — archivo de configuración (recomendada):**

La plantilla real ya está en [`desplegar/ngrok.yml`](desplegar/ngrok.yml) con tu
dominio. Cópiala a la carpeta de config de ngrok (te dice cuál `ngrok config check`):

| Instalación | Ruta |
|---|---|
| **Windows (MSIX, la tuya)** | `C:\Users\<usuario>\AppData\Local\Packages\ngrok.ngrok_1g87z0zv29zzc\LocalCache\Local\ngrok\ngrok.yml` |
| Windows (zip) | `C:\Users\<usuario>\AppData\Local\ngrok\ngrok.yml` |
| Linux/macOS | `~/.config/ngrok/ngrok.yml` |

Ojo: al fusionar con el actual, **conserva la línea del `authtoken`**. Arranca con:

```powershell
ngrok start nova
```

Deja esa terminal **abierta**: es el túnel. Verás la línea
`Forwarding https://riptide-slingshot-mandarin.ngrok-free.dev -> http://localhost:5000`.

## Paso 2 — Pon la URL fija en el `.env` (ya hecho)

Con la URL fija, Nova **no** la detecta sola (no depende de la API de ngrok) y
registra el webhook siempre contra el mismo sitio:

```ini
WEBHOOK_BASE_URL=https://riptide-slingshot-mandarin.ngrok-free.dev
```

## Paso 3 — Arranca Nova y registra el webhook (una sola vez)

En **otras terminales** (el túnel sigue en la primera):

```powershell
# Terminal 2 — servidor
python servidor.py

# Terminal 3 — webhook de Telegram (solo la primera vez)
.\.venv\Scripts\python.exe registrar_webhook.py
```

Como `WEBHOOK_BASE_URL` ya está fijada, `registrar_webhook.py` registra
`https://riptide-slingshot-mandarin.ngrok-free.dev/telegram/webhook` y **no hay
que repetirlo** aunque reinicies ngrok o el servidor.

> 💡 Atajo: doble clic en [`desplegar/arrancar-nova.bat`](desplegar/arrancar-nova.bat)
> levanta el túnel **y** el servidor de una vez.

Comprueba el estado:

```powershell
.\.venv\Scripts\python.exe registrar_webhook.py --info
```

## Paso 4 — Empieza a usarlo en Telegram

Con el túnel y el servidor corriendo, **todo se maneja desde tu grupo**:

1. **Abre tu grupo** («#1 Transfer > Nova») en Telegram.
2. Escribe **`/estado`** → el bot contesta con el estado actual. Si responde, ¡ya está conectado! 🎉
3. Escribe **`/agregar +573001234567`** para añadir números a la lista.
4. Escribe **`/llamar`** → sale un menú con tus números; tocas uno y Nova marca.
   Durante cada llamada se usa **UN SOLO mensaje** que se va **editando** con un
   **⏱️ cronómetro en vivo**, las horas de cada número y el estado
   (`🟢 en llamada` → `🟡 esperando validación` → `🔴 llamada finalizada`),
   más los botones ✅/❌ para validar.
5. **`/apagar`** detiene el ciclo cuando quieras.

> 💡 Al escribir `/` en el grupo verás el **menú de comandos** publicado.
>
> 🔍 Si algo no responde, corre el diagnóstico (te dice exactamente qué falta):
>
> ```powershell
> .\.venv\Scripts\python.exe diagnostico_telegram.py
> ```
>
> ⚠️ Si el bot no "ve" los que escribís en el grupo (solo responde si lo mencionas),
> en **@BotFather** usa `/setprivacy` → tu bot → **Disable**.

---

## Arrancar / parar Nova cuando quieras (Windows)

Ya tienes **3 botones** en la carpeta [`desplegar/`](desplegar). Doble clic y listo:

| Botón | Qué hace |
|---|---|
| 🟢 [`arrancar-nova.bat`](desplegar/arrancar-nova.bat) | Levanta el **túnel** y el **servidor**. Deja la ventana **abierta**. |
| 🔴 [`parar-nova.bat`](desplegar/parar-nova.bat) | Detiene servidor + túnel. |
| 🔁 [`arranque-automatico.bat`](desplegar/arranque-automatico.bat) | Hace que Nova arranque **solo** al encender el PC (`/quitar` para deshacerlo). |

**Uso diario:** doble clic en `arrancar-nova.bat` → aparece una ventana que muestra
`Nova -> https://riptide-slingshot-mandarin.ngrok-free.dev`. Mientras esa ventana
esté abierta, Nova está funcionando. Para pararlo, ciérrala o usa `parar-nova.bat`.

> ⚠️ Si cierras la ventana de negra (la del servidor), el lanzador cierra también
> ngrok automáticamente. No hace falta hacer nada más.
>
> ℹ️ El **webhook de Telegram no hay que re-registrarlo**: como el dominio es fijo,
> `registrar_webhook.py` se corre **una sola vez** en la vida.

### Atajo todavía más rápido (sin ventana)

Crea un acceso directo en el Escritorio → clic derecho → **Nuevo → Acceso directo**
→ destino:

```
"C:\Users\AMD\Desktop\llamadas\nova-bot-llamadas-main\nova-bot-llamadas-main\desplegar\arrancar-nova.bat"
```

Y para pararlo, otro acceso directo a `parar-nova.bat`.

### Arranque 100 % solo (sin tocar nada)

Doble clic una vez en [`arranque-automatico.bat`](desplegar/arranque-automatico.bat):
pone un acceso directo en `shell:startup`, así Nova sube al iniciar sesión.
Para deshacerlo: `arranque-automatico.bat /quitar`.

## Cómo dejarlo en Linux (systemd)

En un equipo Linux (o una Raspberry) que puedas dejar encendido:

```bash
# Nova
sudo cp desplegar/nova.service /etc/systemd/system/
sudo systemctl enable --now nova

# Túnel ngrok
sudo cp desplegar/nova-ngrok.service /etc/systemd/system/
sudo systemctl enable --now nova-ngrok
```

(`nova.service` está pensado para un VPS con dominio propio; para ngrok basta con
apuntar `WorkingDirectory` a la carpeta del proyecto y no tocar `WEBHOOK_BASE_URL`
más allá de la URL de ngrok.)

---

## Límites del plan gratuito (lo que debes saber)

| Recurso | Límite gratis | ¿Alcanza para Nova? |
|---|---|---|
| Endpoints online | 3 | Sí (usamos 1) |
| Endpoints | **no expiran** (24/7) | Sí |
| Dominio fijo (dev domain) | 1 | Sí |
| Datos de salida | 1 GB / mes | De sobra (voz y texto pesan poco) |
| Peticiones HTTP | 20.000 / mes | Sí |
| HTTPS automático | Incluido | Sí |
| Tarjeta de crédito | **No** | — |

- La **página intermedia** ("You are about to visit…") que pone ngrok al abrir
  un endpoint en el navegador **no afecta** a Nova: Twilio y Telegram son
  clientes de API (no navegadores) y la saltan solos. Si abres la URL tú a mano
  en el navegador y la ves, es normal.
- Si agotas los 20.000 requests/mes, el endpoint deja de responder hasta el
  siguiente ciclo. Para el uso normal de Nova no llegas ni de lejos.

---

## Problemas típicos

| Síntoma | Causa / Solución |
|---|---|
| `ERR_NGROK_8012` o "failed to start" | El dominio ya está ocupado por otro agente. Cierra el otro ngrok. |
| Telegram no responde | `registrar_webhook.py --info` y revisa que la URL sea la fija correcta |
| La URL cambia al reiniciar | No estás usando `--url`/`domain`. Repite el Paso 1 |
| Twilio dice error 11200 | `/voice/...` no responde: revisa que `servidor.py` esté corriendo |
| `Faltan credenciales en .env` | Completa Twilio (3) + Telegram (2) en el `.env` |

## Por qué no un "VPS gratis"

Los planes gratuitos de siempre ya casi no existen y, peor, **duermen** al no
recibir tráfico: un servidor dormido **mata la llamada** cuando Twilio marca.
Por eso ngrok (tu PC siempre encendida + dominio fijo gratis) es la vía gratuita
más fiable hoy. Si algún día quieres independencia total de tu PC, mira
[DESPLIEGUE-VPS.md](DESPLIEGUE-VPS.md).