"""Lectura del buzón de correo (IMAP) para importar las facturas de proveedores que llegan por email.

Funciona con Gmail usando una "contraseña de aplicación" (requiere verificación en dos pasos) y con
cualquier proveedor IMAP. Solo se descargan adjuntos .zip y .xml; cada mensaje se procesa una sola vez.
"""
import email
import hashlib
import imaplib
import re
from dataclasses import dataclass, field
from email.header import decode_header

from . import contab
from .importacion import importar_archivo
from .models import CorreoProcesado

SERVIDORES = {"gmail": ("imap.gmail.com", 993), "outlook": ("outlook.office365.com", 993)}
CLAVES = ("correo_servidor", "correo_usuario", "correo_clave", "correo_carpeta", "correo_dias", "correo_filtro")


@dataclass
class ResultadoCorreo:
    revisados: int = 0
    importados: int = 0
    mensajes: list = field(default_factory=list)
    error: str | None = None


def configuracion(session):
    c = {k: contab.config(session, k, "") for k in CLAVES}
    c["activo"] = bool(c["correo_usuario"] and c["correo_clave"])
    return c


def _decodificar(texto):
    partes = []
    for valor, cod in decode_header(texto or ""):
        partes.append(valor.decode(cod or "utf-8", "replace") if isinstance(valor, bytes) else valor)
    return "".join(partes)


def _clave_mensaje(message_id, uid) -> str:
    """Identificador estable y corto del mensaje: hash del Message-ID o, si no trae, del UID IMAP."""
    base = (message_id or "").strip() or f"uid-{uid.decode() if isinstance(uid, bytes) else uid}"
    return "sha1:" + hashlib.sha1(base.encode("utf-8", "replace")).hexdigest()


def _servidor(nombre):
    nombre = (nombre or "gmail").strip().lower()
    if nombre in SERVIDORES:
        return SERVIDORES[nombre]
    host, _, puerto = nombre.partition(":")
    return host, int(puerto or 993)


def conectar(session):
    c = configuracion(session)
    host, puerto = _servidor(c["correo_servidor"])
    imap = imaplib.IMAP4_SSL(host, puerto, timeout=15)
    imap.login(c["correo_usuario"], c["correo_clave"])
    return imap, c


def explicar_error(e: Exception) -> str:
    """Mensaje entendible para los errores más comunes al conectar al buzón."""
    texto = str(e)
    if isinstance(e, imaplib.IMAP4.error) and ("AUTHENTICATIONFAILED" in texto.upper() or "Invalid credentials" in texto):
        return "usuario o contraseña rechazados. En Gmail debe usar una contraseña de aplicación, no la del correo."
    if isinstance(e, (OSError, TimeoutError)):
        return "no hay conexión con el servidor (revise internet, el nombre del servidor y el puerto)."
    return texto


def probar(session) -> str:
    imap, c = conectar(session)
    try:
        estado, _ = imap.select(c["correo_carpeta"] or "INBOX", readonly=True)
        if estado != "OK":
            return f"Conectado, pero la carpeta '{c['correo_carpeta']}' no existe."
        return "Conexión correcta."
    finally:
        imap.logout()


def revisar(session, limite=50) -> ResultadoCorreo:
    """Busca mensajes recientes con adjuntos XML/ZIP y los importa como gastos."""
    res = ResultadoCorreo()
    try:
        imap, c = conectar(session)
    except Exception as e:  # noqa: BLE001
        res.error = f"No se pudo conectar al correo: {explicar_error(e)}"
        return res
    try:
        estado, datos = imap.select(c["correo_carpeta"] or "INBOX", readonly=True)
        if estado != "OK":
            res.error = f"La carpeta '{c['correo_carpeta'] or 'INBOX'}' no existe en el buzón."
            return res
        dias = int(c["correo_dias"] or 30)
        from datetime import date, timedelta
        desde = (date.today() - timedelta(days=dias)).strftime("%d-%b-%Y")
        filtro = (c["correo_filtro"] or "").strip()
        if filtro:
            # El asunto puede llevar tildes: se envía como literal UTF-8 (imaplib solo codifica ASCII).
            imap.literal = filtro.encode("utf-8")
            estado, datos = imap.uid("search", "CHARSET", "UTF-8", "SINCE", desde, "SUBJECT")
        else:
            estado, datos = imap.uid("search", None, "SINCE", desde)
        if estado != "OK":
            res.error = "La búsqueda en el buzón falló."
            return res
        ids = datos[0].split()[-limite:]
        procesados = {m.mensaje_id for m in session.query(CorreoProcesado.mensaje_id)}
        for uid in ids:
            estado, partes = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT)])")
            if estado != "OK" or not partes or not isinstance(partes[0], tuple):
                continue
            cab = email.message_from_bytes(partes[0][1])
            mid = _clave_mensaje(cab.get("Message-ID"), uid)
            if mid in procesados:
                continue
            procesados.add(mid)
            estado, partes = imap.uid("fetch", uid, "(BODY.PEEK[])")
            if estado != "OK" or not partes or not isinstance(partes[0], tuple):
                continue
            msg = email.message_from_bytes(partes[0][1])
            asunto = _decodificar(msg.get("Subject"))
            res.revisados += 1
            lineas = []
            for parte in msg.walk():
                nombre = _decodificar(parte.get_filename() or "")
                if not nombre or not re.search(r"\.(zip|xml)$", nombre, re.I):
                    continue
                contenido = parte.get_payload(decode=True)
                if not contenido:
                    continue
                for r in importar_archivo(session, nombre, contenido, origen="correo"):
                    lineas.append(f"{'OK' if r.ok else 'NO'} {r.archivo}: {r.mensaje}")
                    if r.ok:
                        res.importados += 1
            session.add(CorreoProcesado(mensaje_id=mid, asunto=asunto[:250],
                                        resultado="\n".join(lineas) or "Sin adjuntos XML/ZIP"))
            session.commit()
            if lineas:
                res.mensajes.append((asunto, lineas))
    except Exception as e:  # noqa: BLE001
        session.rollback()
        res.error = f"Error leyendo el correo: {e}"
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return res
