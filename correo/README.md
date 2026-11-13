# Mi correo: Zoho Mail + resúmenes automáticos con Claude

Cada 30 minutos, **sin que tengas nada abierto**:

1. Se traen los correos nuevos de tu Zoho Mail (bandeja de entrada y enviados).
2. Claude los lee y mantiene una **ficha por contacto**: resumen de la relación, pendientes,
   datos clave (montos, facturas, RUT, acuerdos) y plazos.
3. Se publica tu página privada: `https://nicolasanavalon.github.io/sii-dte/correo/`
   - **Súper claro**: plazos, lo que tienes que responder, lo importante de la semana y los pendientes.
   - **Contactos**: la ficha de cada persona o empresa, con todos sus correos.
   - **Bandeja**: todos los correos con su resumen; tócalos para leer el texto completo.
4. Si llega algo **importante**, te avisa por Telegram (el mismo bot de los reportes del DTE).

```
GitHub Actions (cada 30 min)
  └─ correo/sync.py ─ API de Zoho Mail (solo lectura)
                    ─ Claude: resume y actualiza la ficha de cada contacto
                    ─ cifra todo con tu contraseña ─> GitHub Pages (/correo/)
                    ─ aviso por Telegram si hay algo importante
```

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
| `ANTHROPIC_API_KEY` | Tu API key de https://console.anthropic.com (Settings → API Keys) |

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

### 4. Primera sincronización

Repo → **Actions** → **Correo: sincronizar** → **Run workflow**. La primera vez trae los últimos
30 días (máximo 150 correos por corrida; el resto entra en las siguientes). Cuando termine, abre
`https://nicolasanavalon.github.io/sii-dte/correo/` e ingresa tu `CORREO_CLAVE`.

Desde ahí corre sola cada 30 minutos.

## Ajustes opcionales (Settings → Secrets and variables → Actions → **Variables**)

| Variable | Para qué | Por defecto |
|---|---|---|
| `ZOHO_DC` | Región de tu Zoho (`com`, `eu`, `in`, `com.au`, …) | `com` |
| `CORREO_DIAS_INICIALES` | Días hacia atrás en la primera importación | `30` |
| `CORREO_MAX_POR_CORRIDA` | Tope de correos nuevos por corrida | `150` |
| `CORREO_MODELO` | Modelo de Claude | `claude-opus-5-5` |
| `CORREO_URL` | Si usas un dominio propio para Pages | `https://USUARIO.github.io/REPO/correo/` |

Para no recibir avisos por Telegram, crea la variable `CORREO_TELEGRAM` = `0`.

## Costos aproximados

- **Zoho**: $0 si la API funciona con el plan gratis; si no, Mail Lite ~US$1/mes.
- **GitHub Actions/Pages**: gratis si el repo es público. Si es privado, el plan gratis incluye
  2.000 minutos al mes y esta sincronización usa ~1 minuto por corrida: cada 30 minutos son ~1.400
  al mes, sumados a los reportes del DTE. Si se acercan al límite, baja la frecuencia en el `cron`
  de `.github/workflows/correo_sync.yml` (por ejemplo, cada hora: `"7 * * * *"`).
- **Claude**: se paga por uso. Una ficha se actualiza con unos pocos miles de tokens; para un volumen
  normal de correo, del orden de US$5–15 al mes. Puedes ponerle un tope en console.anthropic.com.

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
