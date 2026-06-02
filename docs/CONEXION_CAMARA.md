# Guía de conexión de la cámara Hikvision

Esta guía explica cómo integrar una cámara/terminal de reconocimiento facial Hikvision
con el sistema de Control de Asistencia Guayamuri: enrolar empleados, recibir las
marcaciones y qué hacer si el servidor pierde conexión.

> **Estado actual:** el sistema ya está preparado para la cámara. Mientras no se
> configuren las credenciales, todo funciona con el **simulador** y los datos de los
> empleados (foto, tarjeta) quedan guardados con estado de cámara *"pendiente"*.

---

## 1. Requisitos previos

- La cámara/terminal y el equipo servidor deben estar en la **misma red local** (LAN).
- Conocer la **IP de la cámara** (ej. `192.168.1.64`), el **usuario** y la **clave** de administrador del equipo.
- La cámara debe tener habilitado el protocolo **ISAPI** (viene activo por defecto en la mayoría de modelos de control de acceso facial: serie DS-K1T, DS-K5xxx, etc.).
- Asignar al servidor una **IP fija** en la LAN para que la cámara siempre sepa a dónde enviar los eventos.

---

## 2. Configurar las credenciales en el servidor

Edita el archivo `backend/.env` y completa los datos de la cámara (quita el `#` del inicio):

```env
HIKVISION_HOST="192.168.1.64"     # IP de la cámara, sin http://
HIKVISION_PORT=80                  # 80 por defecto (443 si usa HTTPS)
HIKVISION_USER="admin"
HIKVISION_PASSWORD="tu_clave"
HIKVISION_USE_HTTPS=false           # true si la cámara usa HTTPS
```

Reinicia el backend después de guardar. Con esto el sistema podrá:
- **Enrolar** empleados (subir su rostro a la cámara).
- **Recuperar** eventos almacenados en la cámara (reconciliación, ver sección 5).

---

## 3. Enrolar empleados (registrar su rostro en la cámara)

1. Entra a la página **Empleados**.
2. Crea o edita un empleado y **sube su foto** del rostro (JPG o PNG, frontal, buena iluminación).
3. Pulsa el botón **📷 Cámara** de ese empleado.
   - El sistema da de alta a la persona en el equipo (`employeeNo` = Person ID) y sube su rostro.
   - El estado cambia a **Sincronizado** ✅.
4. Si cambias el nombre, la tarjeta o la foto, el estado vuelve a **Pendiente** para que lo vuelvas a sincronizar.

> Internamente se usan los endpoints ISAPI:
> `POST /ISAPI/AccessControl/UserInfo/Record` (alta de persona) y
> `POST /ISAPI/Intelligent/FDLib/FaceDataRecord` (carga del rostro).
> Algunos modelos usan rutas ligeramente distintas; se ajustan en
> `backend/app/services/hikvision_client.py`.

---

## 4. Recibir las marcaciones en tiempo real

Cuando una persona es reconocida, la cámara debe avisar al sistema. Hay dos formas:

### Opción A — Notificación HTTP en tiempo real (push)
En la configuración de la cámara (vía navegador o software iVMS/SADP), en
**Configuración → Red → Servicio de notificación / HTTP Listening / Centro de alarmas**,
agrega el servidor como destino:

- **URL / Host de destino:** `http://IP_DEL_SERVIDOR:8000/api/hikvision/event`
- **Método:** HTTP POST

> ⚠️ **Importante:** el formato de evento nativo de Hikvision (`AccessControllerEvent`
> con campo `employeeNoString`) es más complejo que el payload simple que usa el
> simulador (`{"person_id": "..."}`). Para producción con la cámara real, el endpoint
> `/api/hikvision/event` debe **mapear** el JSON de Hikvision al `person_id`. Esto se
> hace en `backend/app/main.py` (función `receive_hikvision_event`) en cuanto tengamos
> el equipo a mano para ver el formato exacto que envía.

### Opción B — Sondeo / reconciliación (pull) — **recomendado como respaldo**
El sistema consulta periódicamente a la cámara los eventos almacenados y los importa.
Es la opción más **robusta** y la que resuelve la pérdida de conexión (sección 5).

---

## 5. ¿Qué pasa si el servidor pierde conexión? (pregunta clave)

**Sí: la cámara sigue registrando aunque el servidor esté caído.** Los terminales
faciales Hikvision de control de acceso son **autónomos**: guardan localmente cada
reconocimiento en su propia memoria interna (miles de eventos), independientemente de
si hay red o si el servidor responde.

El detalle está en **cómo recuperar esos registros** cuando el servidor vuelve:

- La **notificación en tiempo real (push)** NO garantiza que se reenvíen los eventos que
  no se pudieron entregar mientras el servidor estaba caído. Depende del modelo y no es
  fiable por sí sola.
- Por eso el sistema implementa una **reconciliación (pull)**: le pide a la cámara los
  eventos de un rango de fechas y **inserta solo los que falten**, sin duplicar.

### Cómo usar la reconciliación

Endpoint ya implementado:

```
POST /api/hikvision/pull-events?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
```

- Sin fechas, trae los **últimos 7 días**.
- Evita duplicados comparando persona + fecha/hora exacta.
- Asigna entrada/salida por alternancia, igual que las marcaciones normales.
- Si la cámara no está configurada, responde `"pendiente"` sin error.

Ejemplo:
```bash
curl -X POST "http://localhost:8000/api/hikvision/pull-events?start_date=2026-06-01&end_date=2026-06-02"
```

### Reconciliación automática (ya integrada en el backend) ✅
El servidor ejecuta la reconciliación **solo, en segundo plano**, sin que tengas que
hacer nada:

- Hace **una pasada al arrancar** (recupera lo ocurrido mientras estuvo caído).
- Luego repite **cada X minutos**.

Se controla con estas variables en `backend/.env` (valores por defecto entre paréntesis):

```env
RECONCILE_ENABLED=true            # activa/desactiva el job (true)
RECONCILE_INTERVAL_MINUTES=10     # frecuencia en minutos (10)
RECONCILE_LOOKBACK_DAYS=2         # ventana hacia atrás que revisa cada ciclo (2)
```

> Si la cámara aún no está configurada (`HIKVISION_*` vacíos), el job simplemente no
> hace nada. En cuanto completes las credenciales y reinicies, empieza a recuperar
> marcaciones automáticamente. Así **no se pierde ninguna asistencia**, incluso con
> cortes prolongados. Ajusta `RECONCILE_LOOKBACK_DAYS` si prevés cortes de varios días.

### Alternativa: Programador de tareas de Windows
Si prefieres NO usar el job interno (`RECONCILE_ENABLED=false`), puedes programar la
reconciliación con el Programador de tareas de Windows usando el script incluido
`backend/scripts/reconciliar.ps1` (ver ese archivo para las instrucciones de registro).

> Límite a tener en cuenta: la memoria de la cámara es finita (depende del modelo, suele
> ser de decenas de miles de eventos). Si el servidor estuviera caído durante semanas con
> muchísimas marcaciones, los registros más antiguos podrían sobrescribirse en la cámara.
> Con reconciliación frecuente esto no representa un problema en la práctica.

---

## 6. Lista de verificación de puesta en marcha

- [ ] Servidor con IP fija en la LAN y backend corriendo (`python run.py`).
- [ ] Credenciales `HIKVISION_*` completadas en `backend/.env`.
- [ ] Empleados creados con foto y **sincronizados** con la cámara (estado ✅).
- [ ] Notificación HTTP de la cámara apuntando a `/api/hikvision/event` (opción A), y/o
      reconciliación programada (opción B).
- [ ] Probar una marcación real y verificarla en **Monitoreo En Vivo**.
- [ ] Verificar el reporte de horas y la exportación a Excel.
```
