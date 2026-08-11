# DTE SII — actualización automática

Baja tu **Registro de Compra (DTE)** del SII una vez al día y publica un dashboard web
que se actualiza **solo**, sin tu computador prendido. Corre en **GitHub Actions** (gratis) y
se publica en **GitHub Pages**.

## Cómo funciona

```
GitHub Actions (cron diario)
   └─ fetch_sii.py  →  entra al SII con tu Clave (guardada como secreto)
                       baja el detalle de compras (mes actual + anterior)
                       regenera index.html con los datos y la fecha de actualización
   └─ publica index.html en GitHub Pages  →  https://TU-USUARIO.github.io/TU-REPO/
```

Tu Clave Tributaria vive **solo** como *secreto cifrado* de GitHub. No está en el código,
ni en el HTML, ni aparece en los logs.

## Puesta en marcha (una sola vez)

1. **Crea un repositorio** en GitHub y sube estos archivos
   (`fetch_sii.py`, `dashboard_template.html`, `requirements.txt`, `.github/…`, este README).
2. **Guarda tus credenciales como secretos:**
   repo → *Settings* → *Secrets and variables* → *Actions* → *New repository secret*:
   - `SII_RUT`  → tu RUT con dígito verificador, ej. `77752795-9`
   - `SII_CLAVE` → tu Clave Tributaria
3. **Activa Pages:** *Settings* → *Pages* → *Source* = **GitHub Actions**.
4. **Primera corrida:** pestaña *Actions* → *Actualizar DTE desde SII* → *Run workflow*.
   Cuando termine, tu sitio queda en `https://TU-USUARIO.github.io/TU-REPO/`.

Desde ahí se actualiza solo todos los días. Puedes cambiar la hora/frecuencia editando el
`cron` en `.github/workflows/update.yml`, o cuántos meses baja con el secreto/variable `SII_MESES`.

## ⚠️ Privacidad — importante

GitHub Pages en un repositorio **público** es **visible para cualquiera con el link**:
quedaría expuesto el detalle de tus compras (proveedores, RUT, montos). Opciones:

- **Repo privado + Pages privado** (requiere plan pago de GitHub) → el sitio pide login.
- **Aceptar que sea público** con una URL difícil de adivinar (seguridad débil, pero simple).
- **Otro hosting con contraseña** si necesitas control de acceso real (te puedo ayudar a montarlo).

Elige antes de publicar. Por defecto, un repo público = datos públicos.

## Probar/reconstruir localmente

```bash
pip install -r requirements.txt
python -m playwright install chromium

# Bajar del SII y generar index.html:
export SII_RUT="77752795-9"; export SII_CLAVE="········"
python fetch_sii.py

# Solo reconstruir el HTML desde los CSV ya bajados (sin tocar el SII):
python fetch_sii.py --build-only
```

## Si el login falla

El SII podría cambiar el formulario o pedir un **captcha** (que, por diseño, no se puede
resolver de forma automática). Si eso ocurre, la corrida falla y sube un artefacto
`login_error.png` con una captura para diagnóstico. En ese caso, ajustamos los selectores
del login o volvemos al modo semiautomático (botón + login manual).
