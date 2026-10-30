# MovistarEPG

Guía XMLTV de Movistar+ generada desde la API de rejilla OTT
(`ottcache.dof6.com`, webplayer "DIFUSION") con fallback a la web
`movistarplus.es` para los canales que la API no trae (normalmente TDT
regionales).

## Ficheros publicados

| Fichero | Ventana | Contenido aprox. |
| --- | --- | --- |
| `epg1.xml.gz` | 1 día (hoy) | ~3.200 pases |
| `epg2.xml.gz` | 2 días (hoy + mañana) | ~6.500 pases |
| `epg7.xml.gz` | 7 días | ~23.500 pases |

Los tres se regeneran cada día (ventana `[hoy, hoy+N)`) y se publican en este
repositorio. Los canales quedan identificados por el `tvg-id` de la lista M3U
(código de Movistar: `TVE`, `A3`, `MPLUSEST`, ...) y como `display-name` llevan
el nombre de la lista; el logo es el oficial de cada canal.

## Uso del script

```bash
pip install -r requirements.txt   # no deps externas, solo stdlib
python3 epg.py --m3u inicial.m3u --out . --days 1 2 7 [--bind IP.salida]
```

- `--m3u` lista M3U con los canales (lee `tvg-id`, `tvg-name`, `tvg-logo` y el
  nombre tras la coma).
- `--days` ventanas en días; escribe `epg1.xml.gz`, `epg2.xml.gz`, ... y su
  equivalente `.xml` sin comprimir.
- `--bind` `(Opcional)` IP de salida si las peticiones deben salir por un túnel/IP
  concreta.

## Consumo

En la lista M3U, la cabecera apunta al fichero deseado:

```
#EXTM3U url-tvg="https://raw.githubusercontent.com/mateodd1/MovistarEPG/main/epg7.xml.gz"
```

## Renovación

Ejecuta el script con cron (p. ej. 4:10 EDT de la mañana) y haz commit+push de
los seis ficheros resultantes. Ventana desde las 0:00 CET de hoy; los pases que
quedan por delante de la medianoche venían de la víspera, la API los trae igual.
