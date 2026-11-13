# Mi correo: Zoho Mail + resúmenes automáticos con Claude

> **Bandeja Anasep** → `https://nicolasanavalon.github.io/sii-dte/correo/` (se abre con tu `CORREO_CLAVE`).
> Cada conversación trae el resumen de **toda la cadena** (incluidos los mensajes citados) y el texto de sus
> **adjuntos** (PDF, Excel, Word), más un **buscador** y una caja para **preguntarle a la IA**.
> La IA es **Groq, en su plan gratis** (no usa Claude ni cobra nada): resume en GitHub Actions cada 30 minutos y
> responde las preguntas desde la página. Su clave viaja cifrada dentro de la página: solo funciona con tu contraseña.
>
> **Activar la IA gratis (una vez):** crea una cuenta en https://console.groq.com, ve a **API Keys** y crea una
> clave. Guárdala como secreto `GROQ_API_KEY` en el repo (Settings → Secrets and variables → Actions). Si el
> plan gratis llega a su límite del día, lo pendiente se resume en la siguiente corrida: nunca se cobra.

Cada 30 minutos, **sin que tengas nada abierto**:

1. Se traen los correos nuevos de tu Zoho Mail (bandeja de entrada y enviados), cada 30 minutos.
2. Cada 2 horas, una **sesión programada de Claude** (con tu plan de Claude, sin costo extra) los lee
   y mantiene una **ficha por contacto**: resumen de la relación, pendientes, datos clave (montos,
   facturas, RUT, acuerdos) y plazos.
3. Se publica tu página privada: `https://nicolasanavalon.github.io/sii-dte/correo/`
   - **Súper claro**: plazos, lo que tienes que responder, lo importante de la semana y los pendientes.
   - **Contactos**: la ficha de cada persona o empresa, con todos sus correos.
   - **Bandeja**: todos los correos con su resumen; tócalos para leer el texto completo.
4. Si llega algo **importante**, te avisa por Telegram (el mismo bot de los reportes del DTE).

```
GitHub Actions (cada 30 min, gratis)
  └─ correo/sync.py ─ API de Zoho Mail (solo lectura)
                    ─ cifra todo con tu contraseña ─> GitHub Pages (/correo/)

Sesión programada de Claude (cada 2 h, con tu plan)  ── ver TURNO_CLAUDE.md
  └─ pide los correos sin resumen (llegan sellados con una llave que solo vive ese turno)
  └─ los resume y devuelve sellado ─> GitHub los guarda, publica y avisa por Telegram
```

La sesión de Claude **no guarda ninguna contraseña**: en cada turno crea una llave de un solo uso,
y GitHub le entrega los correos cifrados con esa llave por la rama `correo-intercambio`.

## 🔒 Privacidad

- La página está **cifrada con tu contraseña** (AES-256). Sin ella, lo que se publica es ilegible,
  aunque alguien tenga el link. La contraseña nunca se guarda en el código ni en la página.
- Los correos **no se guardan en el repositorio**: viven cifrados dentro de la misma página publicada.
- El acceso a Zoho es de **solo lectura**: no puede borrar, mover ni enviar correos.
- Si olvidas la contraseña, no hay forma de recuperar los datos (se vuelven a importar desde Zoho).

## Puesta en marcha (una sola vez, ~15 minutos)

### 1. Crear la "app" en Zoho (gratis)

1. Entra a **https://api-console.zoho.com** con tu cuenta de Zoho Mail.
2. **Add Client** (o *Get Started*) → elige **Self Client** → **Create** → **OK**.
3. En la pestaña **Client Secret** verás **Client ID** y **Client Secret**. Déjala abierta.

> Si tu Zoho Mail se abre en `mail.zoho.eu`, `mail.zoho.in`, etc. (no `.com`), crea también la
> variable `ZOHO_DC` del paso 2 con `eu`, `in`, etc.

### 2. Guardar las claves en GitHub

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secreto | Qué poner |
|---|---|
| `ZOHO_CLIENT_ID` | El Client ID del paso 1 |
| `ZOHO_CLIENT_SECRET` | El Client Secret del paso 1 |
| `CORREO_CLAVE` | **Una contraseña que inventes** (mínimo 8 caracteres). Con ella abrirás la página. ¡Anótala! |

`TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` ya existen por los reportes del DTE: se reutilizan.

### 3. Conectar Zoho (aquí se prueba si funciona con el plan gratis)

1. Vuelve a api-console.zoho.com → tu Self Client → pestaña **Generate Code**.
2. En **Scope** pega exactamente:
   ```
   ZohoMail.accounts.READ,ZohoMail.folders.READ,ZohoMail.messages.READ
   ```
3. **Time Duration**: 10 minutes. **Scope Description**: `correo`. → **Create** → copia el código.
4. Rápido (vence en 10 min): repo → **Actions** → **Correo: conectar Zoho** → **Run workflow** →
   pega el código → **Run workflow**.
5. Abre la corrida y mira el resultado:
   - ✅ *"¡La API funciona con tu plan!"* → listo, sigue al paso 4.
   - ❌ Si dice que tu plan no lo permite, la API no está disponible en el plan gratis: hay que pasar
     a **Zoho Mail Lite** (~US$1/mes) y repetir este paso. El resto queda igual.

### 4. Activar los resúmenes

Claude crea la tarea programada cada 2 horas que sigue `correo/TURNO_CLAUDE.md`. La primera vez se
traen los últimos 30 días; los resúmenes se completan en los primeros turnos (40 correos por vuelta).
Abre `https://nicolasanavalon.github.io/sii-dte/correo/` e ingresa tu `CORREO_CLAVE`.

## Ajustes opcionales (Settings → Secrets and variables → Actions → **Variables**)

| Variable | Para qué | Por defecto |
|---|---|---|
| `ZOHO_DC` | Región de tu Zoho (`com`, `eu`, `in`, `com.au`, …) | `com` |
| `CORREO_DIAS_INICIALES` | Días hacia atrás en la primera importación | `30` |
| `CORREO_MAX_POR_CORRIDA` | Tope de correos nuevos por corrida | `150` |
| `CORREO_URL` | Si usas un dominio propio para Pages | `https://USUARIO.github.io/REPO/correo/` |

Para no recibir avisos por Telegram, crea la variable `CORREO_TELEGRAM` = `0`.

## Costos aproximados

- **Zoho**: $0 si la API funciona con el plan gratis; si no, Mail Lite ~US$1/mes.
- **GitHub Actions/Pages**: gratis (el repo es público; los correos van cifrados).
- **Claude**: $0 extra. Los resúmenes los hace una sesión programada con tu plan actual de Claude
  (cuenta para los límites de uso de tu plan). Opcional: si algún día creas el secreto
  `ANTHROPIC_API_KEY`, GitHub resume directamente con la API (pagada por uso) en cada sincronización.

## Probar sin conectar nada

```bash
pip install -r correo/requirements.txt
CORREO_CLAVE=demo12345 python correo/sync.py --demo   # genera correo/index.html con datos ficticios
```

Ábrelo en el navegador con la contraseña `demo12345`.

## Si algo falla

- **"No pude descifrar los datos guardados"**: cambiaste `CORREO_CLAVE`. Vuelve a la contraseña
  anterior o, si quieres empezar de cero, avísame y lo reseteamos.
- **Zoho pide reconectar** (error de autorización): repite el paso 3.
- **"Algunos correos no se pudieron resumir"** en la página: se reintenta solo en la siguiente corrida.
