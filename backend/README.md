# Break & Retest · Roto y retesteo — activación del servicio

La pestaña está integrada en el dashboard existente. **GitHub Pages no ejecuta Python ni mantiene una conexión IBKR.** El análisis continuo necesita este servicio y una sesión de TWS o IB Gateway. El conector de IBKR dentro de ChatGPT sirve para consultas en la conversación; no constituye una credencial que la web pueda reutilizar.

## Estado y límites comprobados

- Motor determinista, cuatro perfiles independientes, historial SQLite e interfaz integrados.
- Sin rutas ni llamadas para colocar, modificar o cancelar órdenes. Conexión `readonly=True` y sin descarga de posiciones, órdenes o cuenta.
- CSPX predeterminado: contrato 76023663, LSEETF, USD. Los metadatos se verifican de nuevo con IBKR al conectar.
- La consulta de IBKR realizada el 8 de octubre de 2026 devolvió velas de CSPX de 2 minutos con `delayed=900`: **15 minutos de retraso**. No se han publicado esos datos como cotizaciones actuales.
- Las pruebas del motor y de la interfaz no sustituyen la aceptación con TWS y permisos de datos reales. Esa conexión sigue pendiente hasta arrancar el servicio en un equipo del usuario.

## Activar en Windows, sin publicar claves

1. Descarga el repositorio completo en tu equipo. Instala Python 3.12 si no lo tienes.
2. Abre TWS o IB Gateway e inicia sesión directamente en IBKR. En configuración API, habilita las conexiones por socket y activa **Read-Only API**. Mantén el acceso del socket limitado a localhost. Verifica el puerto mostrado por tu instalación: TWS suele usar 7497 en simulación y 7496 en real; Gateway suele usar 4002 / 4001. El servicio sigue siendo de solo lectura en ambos casos.
3. En PowerShell, dentro de la carpeta del proyecto, define el puerto de tu TWS y ejecuta:

```powershell
$env:IB_PORT = '7497'
.\backend\start-windows.ps1
```

4. Abre **http://127.0.0.1:8787/#br**. Pulsa **Conectar IBKR**, utiliza `http://127.0.0.1:8787` como dirección y pega la clave que muestra PowerShell. No uses tu contraseña de IBKR. La clave solo permanece en memoria de esa pestaña y cambia en un nuevo proceso si no defines `BR_TOKEN`.
5. El perfil 1 verificará CSPX Londres. En los perfiles 2–4, pulsa **Configurar**, busca el instrumento, selecciona su contrato/bolsa/moneda y guarda. La referencia es opcional.

**No cierres TWS/Gateway ni la ventana del servicio durante la sesión.** Cerrar el navegador no detiene el motor. Suspender el equipo sí lo detiene. Tras desconexión, intenta reconectar; las secuencias antiguas se reconstruyen como históricas, sin señal vigente. No se rellenan huecos de velas inventando precios.

## Usarlo desde la URL pública de GitHub Pages o desde el celular

Hace falta un endpoint **HTTPS privado y autenticado** que alcance este servicio. Instálalo en un equipo persistente conectado a TWS/Gateway; usa un proxy HTTPS con certificado válido y conserva el puerto de TWS inaccesible desde Internet. Configura `BR_ORIGIN=https://smartsoftdevelop-alt.github.io`. La URL y el alojamiento de ese endpoint no han sido provisionados en este repositorio.

En la web pública, pulsa **Conectar IBKR**, introduce esa URL HTTPS y la clave del servicio. No pongas credenciales en `app.js`, GitHub Actions, parámetros URL o archivos publicados. El token es una clave privada del puente, no una credencial de cuenta IBKR. El servicio únicamente entrega datos de mercado e historial de análisis.

## Linux o macOS

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt
export BR_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export IB_PORT=7497
python -m uvicorn backend.service:app --host 127.0.0.1 --port 8787 --workers 1
```

Consulta el token en tu terminal privada. Se necesita Python 3.12 con base de zonas horarias IANA. Un solo proceso de servidor mantiene el motor; no utilizar múltiples workers sobre el mismo servicio.

Variables: `IB_HOST` (127.0.0.1), `IB_PORT` (7497), `IB_CLIENT_ID` (42), `BR_TOKEN` (obligatoria, mínimo 24 caracteres), `BR_ORIGIN`, `BR_DATABASE` (break-retest.sqlite3). La base no se sirve por HTTP ni se incluye en Git.

## Datos y calendario

- Contrato, moneda, zona horaria y bolsa se resuelven con IBKR. Las selecciones ambiguas no se adivinan.
- Sesiones regulares y festivos se consultan mediante `SCHEDULE` de IBKR. Las horas se convierten con `zoneinfo`, sin restar horas fijas.
- Máximo y mínimo previos: exclusivamente las velas de 15 minutos de la sesión regular anterior. Si falta alguna, esos niveles quedan no disponibles.
- EE. UU.: premercado actual desde las 04:00 hasta apertura oficial; ningún rango de apertura sustituye premercado ausente. Sin transacciones disponibles no se inventa su extremo.
- CSPX: rango de apertura con la vela completa de los primeros 15 minutos. Los niveles del día previo se activan desde la apertura. Una vela de 2 minutos que atraviese el minuto 15 no se usa para romper un nivel aún desconocido al abrir esa vela.
- Cotización actual por `reqMktData`; diario, 15 minutos y 2 minutos por `TRADES`. Las velas de 2 minutos incluyen horas extendidas para extraer el premercado, pero las entradas solo se analizan dentro de la sesión regular.
- Solicitudes de 2 minutos en cada nuevo bloque temporal, con revisión del servicio cada 15 segundos; el contexto y el diario se cachean. Puede existir latencia del proveedor. No se promete análisis al milisegundo.
- Para una posible entrada vigente se exige vela reciente, dato en tiempo real verificado y que el retesteo sea la última vela cerrada. Una continuación ya alejada no vuelve a recomendar entrar.
- IBKR puede exigir suscripciones de datos de mercado. Un resultado retrasado, vacío o sin permiso queda explícito.

## Interpretación cuantitativa, política v1

Basada en el PDF aportado y la transcripción del usuario. La automatización hace explícitas estas decisiones de implementación, que no representan reglas universales del autor:

1. Rompimiento: cruce por cierre, desde el cierre anterior o apertura de la primera vela regular. Una apertura con gap al otro lado no se convierte por sí sola en un rompimiento.
2. Retesteo: vela posterior, aproximación desde el lado roto, intersección con el nivel/zona, cierre estricto en el lado defendido. El precio de referencia es ese cierre; no es un precio ejecutado.
3. Tolerancia inicial 0; afecta solo el contacto. Rompimiento e invalidación siempre se evalúan contra el nivel exacto.
4. Invalidación: cierre al lado contrario; un hueco en la secuencia también impide validar la continuación de un setup. No existe salida real ni stop enviado al bróker.
5. Continuación: un extremo nuevo de la sesión posterior al retesteo. El movimiento mostrado es favorable según dirección, no rentabilidad de opciones ni beneficio ejecutado.
6. EMA: inicialización con media simple de 9/21 cierres de la sesión. VWAP usa las medias de transacciones de IBKR ponderadas por volumen, no una media arbitraria de precios. Si faltan datos, no disponible.
7. Volumen superior: al menos 1,5× la media de hasta 20 velas anteriores, con mínimo 5 observaciones. Inferior: menos de 0,75×. Confirmaciones opcionales.
8. Sesgo: máximos y mínimos de las dos últimas velas cerradas de 15 minutos. Gap visible si hay cierre previo. Noticias no integradas: no disponible.
9. Confirmación cruzada: dirección de la vela cerrada de referencia en el mismo instante. No se utiliza la última vela de otra sesión para fingir simultaneidad. Referencia configurable; su ausencia no bloquea el setup.
10. Todos los niveles mantienen máquinas separadas. La tarjeta resume el setup con mayor avance; el historial conserva las transiciones de cada nivel. Estados efímeros intermedios pueden ocurrir en la misma evaluación, pero quedan registrados en orden.

## Historial y verificación

SQLite conserva perfiles, última instantánea y eventos idempotentes por perfil/contrato/bolsa/nivel/fecha/política/tolerancia. Cambiar activo no borra el historial anterior: cada evento conserva la identidad original. La interfaz consulta 100 eventos recientes; Exportar descarga todos. Respalda la base SQLite con el servicio detenido para trasladar el historial a otro equipo.

```bash
python -m unittest discover -s tests -v
```

Las velas sintéticas se usan únicamente dentro de las pruebas. No se envían a la interfaz de producción.

Fuentes técnicas: [IBKR historical bars](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-bars/receiving-historical-bars), [ib_async](https://ib-api-reloaded.github.io/ib_async/api.html), [suscripciones API](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/introduction), [CSPX Londres USD](https://www.londonstockexchange.com/stock/CSPX/ishares/company-page).
