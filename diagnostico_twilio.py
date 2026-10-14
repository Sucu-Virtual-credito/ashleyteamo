"""Diagnostico de Twilio para Nova.

Revisa, en orden, todo lo que hace falta para que Nova pueda LLAMAR de verdad:

    1) Que las credenciales (SID + token) sean validas.
    2) Que el numero TWILIO_NUMBER exista / sea de la cuenta.
    3) Que numeros.txt tenga al menos un destino.
    4) Si los destinos son llamables (en cuenta Trial, deben estar VERIFICADOS).

Uso:

    .\\.venv\\Scripts\\python.exe diagnostico_twilio.py
"""

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

import config as cfg

VERDE = "[OK  ]"
ROJO = "[FALLA]"
AVISO = "[AVISO]"


def _cuenta(cl: Client):
    try:
        return cl.api.accounts(cfg.TWILIO_ACCOUNT_SID).fetch()
    except TwilioRestException as exc:
        return exc


def main() -> int:
    print("=" * 68)
    print(" Diagnostico de Twilio para Nova")
    print("=" * 68)

    sid = (cfg.TWILIO_ACCOUNT_SID or "").strip()
    token = (cfg.TWILIO_AUTH_TOKEN or "").strip()
    numero = (cfg.TWILIO_NUMBER or "").strip()

    # 1) Credenciales --------------------------------------------------------
    if not sid.startswith("AC") or not token:
        print(f"{ROJO} Faltan TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN en el .env.")
        return 1

    cl = Client(sid, token)
    cuenta = _cuenta(cl)
    if isinstance(cuenta, TwilioRestException):
        print(f"{ROJO} Credenciales invalidas: {cuenta.msg}")
        return 1
    trial = cuenta.type == "Trial"
    print(f"{VERDE} Credenciales OK. Cuenta: {cuenta.friendly_name} "
          f"({cuenta.type}, {cuenta.status})")

    # 2) Numero de origen ----------------------------------------------------
    if not numero.startswith("+"):
        print(f"{ROJO} TWILIO_NUMBER='{numero}' debe empezar por + y clave de pais.")
        return 1

    mios = cl.incoming_phone_numbers.list(limit=100)
    mis_nums = [n.phone_number for n in mios]
    if numero in mis_nums:
        n = next(x for x in mios if x.phone_number == numero)
        voz = (n.capabilities or {}).get("voice")
        print(f"{VERDE} TWILIO_NUMBER {numero} pertenece a la cuenta "
              f"{'con VOZ' if voz else 'SIN VOZ'}.")
    elif trial:
        # En Trial, Twilio coloca la llamada desde un "numero de prueba" que
        # NO aparece en la lista normal de numeros. Por eso no es un error.
        print(f"{AVISO} La cuenta Trial no lista {numero} como numero comprado.")
        print("        Es normal: en Trial, la llamada sale desde el 'numero")
        print("        de prueba' de Twilio. Si lo ves en la consola, esta bien.")
        if mis_nums:
            print(f"        Numeros comprados en la cuenta: {', '.join(mis_nums)}")
    elif mis_nums:
        print(f"{ROJO} TWILIO_NUMBER {numero} NO pertenece a esta cuenta.")
        print(f"        Numeros de la cuenta: {', '.join(mis_nums)}")
    else:
        print(f"{ROJO} La cuenta no tiene ningun numero comprado/asignado.")
        print("        Consola -> Phone Numbers -> Buy a number (con Voz).")

    # 3) Destinos en numeros.txt --------------------------------------------
    try:
        destinos = cfg.numeros_a_llamar()
    except ValueError as exc:
        print(f"{ROJO} numeros.txt tiene un error: {exc}")
        return 1
    if not destinos:
        print(f"{ROJO} numeros.txt esta vacio: no hay a quien marcar.")
    else:
        print(f"{VERDE} numeros.txt tiene {len(destinos)} numero(s).")

    # 4) Verificados (clave en Trial) ----------------------------------------
    verificados = {o.phone_number for o in cl.outgoing_caller_ids.list(limit=100)}
    if trial:
        print(f"{AVISO} Cuenta TRIAL: SOLO puedes llamar a numeros VERIFICADOS.")
        print("        Consola -> Phone Numbers -> Verified Caller IDs -> Add")
        print(f"        Verificados ahora: {', '.join(sorted(verificados)) or '(ninguno)'}")
        print("        OJO: el numero con el que te registraste se verifica solo y NO")
        print("        aparece en esta lista, asi que puede funcionar aunque no salga.")
        pendientes = [d for d in destinos if d not in verificados]
        if not pendientes:
            print(f"{VERDE} Todos los numeros de numeros.txt estan verificados.")
        else:
            print(f"{AVISO} No aparecen verificados: {', '.join(pendientes)}")
            print("        Si alguno NO es tu numero de registro, dara error 573002.")
    else:
        print(f"{VERDE} Cuenta de pago: puedes llamar a cualquier numero.")

    # 5) Permisos geograficos (bloquea llamadas con error 21215) --------------
    paises = sorted({_pais_de(d) for d in destinos if _pais_de(d)})
    if not paises:
        print(f"{AVISO} No se pudo deducir el pais de los destinos.")
    else:
        import requests

        auth = (cfg.TWILIO_ACCOUNT_SID, cfg.TWILIO_AUTH_TOKEN)
        bloqueados = []
        for iso in paises:
            try:
                url = (
                    "https://voice.twilio.com/v1/DialingPermissions/"
                    f"Countries/{iso}"
                )
                datos = requests.get(url, auth=auth, timeout=10).json()
            except Exception as exc:  # noqa: BLE001
                print(f"{AVISO} No se pudo consultar el pais {iso}: {exc}")
                continue
            if datos.get("low_risk_numbers_enabled"):
                print(f"{VERDE} Pais {iso}: llamadas PERMITIDAS (voz).")
            else:
                bloqueados.append(iso)
        for iso in bloqueados:
            print(f"{ROJO} Pais {iso}: llamadas BLOQUEADAS (error 21215).")
            print("        Activalo en: console.twilio.com -> Voice -> Settings")
            print("        -> Geographic Permissions -> habilita ese pais.")

    print("=" * 68)
    return 0


def _pais_de(numero: str) -> str:
    """Deduce el ISO (2 letras) del pais de un numero E.164 aproximado."""
    numero = (numero or "").lstrip("+")
    if numero.startswith("57"):
        return "CO"
    if numero.startswith("1"):
        return "US"
    if numero.startswith("52"):
        return "MX"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())