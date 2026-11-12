# MovistarEPG

Guía XMLTV de Movistar+ en ventanas de 1, 2 y 7 días.

| Fichero | Ventana | Contenido aprox. |
| --- | --- | --- |
| `epg1.xml.gz` | 1 día (hoy) | ~3.300 pases |
| `epg2.xml.gz` | 2 días (hoy + mañana) | ~6.700 pases |
| `epg7.xml.gz` | 7 días (hoy .. +6) | ~23.000 pases |

Los tres se regeneran cada día (04:10) y se publican en este repositorio.
Cada canal lleva el `tvg-id` de la lista M3U como id XMLTV, el nombre como
`display-name` y el logo oficial. Los `.xml` sin comprimir no se publican.

## Consumo

```
#EXTM3U url-tvg="https://raw.githubusercontent.com/mateodd1/MovistarEPG/main/epg7.xml.gz"
```

El generador es privado; este repositorio solo contiene los datos.
