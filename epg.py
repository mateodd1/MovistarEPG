#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPG Movistar+: genera XMLTV (1, 2 y 7 días) para los canales de una lista M3U.

Fuentes:
  1) API rejilla OTT  https://ottcache.dof6.com/movistarplus/webplayer/DIFUSION/epg
     (todas las jornadas en una petición; los canales van por CodCadenaTv, que es
     el tvg-id de la lista)
  2) Web movistarplus.es (pages /programacion-tv/<codigo>?fecha=YYYY-MM-DD) para los
     canales que la API no trae (normalmente TDT regionales)

Uso:

  python3 epg.py --m3u inicial.m3u --out . --days 1 2 7 [--bind IP.del.tunel]

Salida (junto a --out):
  epg1.xml.gz   XMLTV de 1 día   (hoy)
  epg2.xml.gz   XMLTV de 2 días  (hoy + mañana)
  epg7.xml.gz   XMLTV de 7 días  (hoy .. +6)

Cada canal sale con su tvg-id (si existe) como id de XMLTV y el tvg-name/nombre
de la lista como display-name, para que los clientes emparejen por cualquiera
de las dos vías. El offset horario es el real de Europe/Madrid por pase.
"""
import argparse
import concurrent.futures
import gzip
import http.client
import json
import re
import ssl
import sys
import time
import urllib.parse
import xml.sax.saxutils as sax
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MAD = ZoneInfo('Europe/Madrid')
HOST_API = 'ottcache.dof6.com'
HOST_WEB = 'www.movistarplus.es'
API_PATH = '/movistarplus/webplayer/DIFUSION/epg'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:154.0) Gecko/20100101 Firefox/154.0'


# ------------------------------------------------------------------ HTTP
def http_get(host, path, bind='', timeout=90):
    """GET https con bind de salida opcional; devuelve utf-8."""
    conn = http.client.HTTPSConnection(
        host, timeout=timeout,
        source_address=(bind, 0) if bind else None,
        context=ssl.create_default_context())
    try:
        conn.request('GET', path, headers={'User-Agent': UA})
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    if resp.status != 200:
        raise RuntimeError(f'HTTP {resp.status} en {host}{path[:100]}')
    return body.decode('utf-8', 'replace')


# ------------------------------------------------------------------ m3u
def canales_de_m3u(path):
    """[(tvg-id, nombre mostrado, logo)] en el orden del fichero."""
    out = []
    for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        if not line.startswith('#EXTINF'):
            continue
        m = re.search(r'tvg-id="([^"]*)"', line)
        logo = re.search(r'tvg-logo="([^"]*)"', line)
        name = line.split(',', 1)[1].strip() if ',' in line else ''
        tid = (m.group(1).strip() if m else '')
        out.append((tid or name, name, logo.group(1) if logo else ''))
    return out


# ------------------------------------------------------------------ API rejilla
def epg_api(tids, dias, bind='', workers=8):
    """Pases de la rejilla por canal para [hoy .. hoy+dias).

    Una peticion por canal en paralelo (la API no aguanta listas largas con
    span grande). Devuelve (pases, canales_fallidos)."""
    desde = datetime.now(MAD).replace(hour=0, minute=0, second=0, microsecond=0)
    span = dias + 1  # la API incluye la víspera
    frm = urllib.parse.quote(desde.isoformat(), safe=':')

    def un_canal(tid):
        q = ('{}?from={}&span={}&channel={}&network=movistarplus&version=8'
             '&mdrm=false&tlsstream=true&demarcation=0').format(
                 API_PATH, frm, span, urllib.parse.quote(tid, safe=''))
        last = None
        for attempt in range(3):
            try:
                return json.loads(http_get(HOST_API, q, bind=bind, timeout=90)), tid
            except Exception as exc:
                last = exc
                time.sleep(1.5 * (attempt + 1))
        return last, tid

    passes, failed, seen, keys = [], set(), set(), set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for data, tid in pool.map(un_canal, tids):
            if not isinstance(data, list):
                print(f'  aviso: API fallo para {tid}: {data}', file=sys.stderr)
                failed.append(tid)
                continue
            for p in data:
                for it in (p if isinstance(p, list) else [p]):
                    if not isinstance(it, dict):
                        continue
                    ini = str(it.get('FechaHoraInicio') or '')
                    if not ini.isdigit():
                        continue
                    fin = str(it.get('FechaHoraFin') or '')
                    if not fin.isdigit():
                        continue
                    key = (tid, int(ini))
                    if key in keys:
                        continue
                    keys.add(key)
                    passes.append({'tid': tid, 'ini': int(ini), 'fin': int(fin),
                                   'titulo': (it.get('Titulo') or '').strip(),
                                   'ep': it.get('TituloEpisodio') or '',
                                   'genero': it.get('GeneroComAntena') or '',
                                   'pais': it.get('Nacionalidad') or ''})
            seen.add(tid)
    return passes, failed, desde


# ------------------------------------------------------------------ web (fallback)
def programas_web(tid, dia, bind=''):
    """Filas [hora HH:MM, titulo, genero, eleid] de la guía web de un canal."""
    path = f'/programacion-tv/{urllib.parse.quote(tid.lower(), safe="")}?fecha={dia:%Y-%m-%d}'
    try:
        raw = http_get(HOST_WEB, path, bind=bind, timeout=60)
    except Exception:
        return None
    out = []
    for chunk in raw.split('<div id="ele-')[1:]:
        if not re.match(r'\d+" class="container_box', chunk):
            continue
        mt = re.search(r'<li class="time">\s*(\d{2}:\d{2})', chunk)
        mi = re.search(r'<li class="title">\s*([^<]{1,200})', chunk)
        mg = re.search(r'<li class="genre">\s*([^<]{1,80})', chunk)
        if mt and mi:
            out.append((mt.group(1),
                        re.sub(r'\s+', ' ', mi.group(1)).strip(),
                        (mg.group(1).strip() if mg else ''),
                        chunk[:10]))
    return out


def encadena(bloques, dia):
    """(hora, titulo, genero, id) de una página -> [(datetime, ...)] monótono."""
    base = dia
    last = None
    rows = []
    for hora, titulo, genero, ev in bloques:
        when = base + timedelta(hours=int(hora[:2]), minutes=int(hora[3:]))
        if when.hour < 6:      # la jornada de guía empieza a las 06:00
            when += timedelta(days=1)
        if last is not None and when < last:
            when += timedelta(days=1)
        rows.append({'dt': when, 'titulo': titulo, 'genero': genero, 'ev': ev})
        last = when
    return rows


def rellena_web(tids, dias, desde, bind=''):
    """Preliminares por canal que no salió en la API (mismo formato ms=)."""
    out = []
    for tid in tids:
        blocks = []
        for offset in range(dias):
            day = desde + timedelta(days=offset)
            rows = programas_web(tid, day, bind=bind)
            if rows:
                blocks += encadena(rows, day)
        if not blocks:
            continue
        seen, rows = set(), []
        for r in blocks:
            k = (r['dt'].isoformat(), r['ev'])
            if k in seen:
                continue
            seen.add(k)
            rows.append(r)
        rows.sort(key=lambda x: x['dt'])
        for i, r in enumerate(rows):
            ini = int(r['dt'].timestamp() * 1000)
            fin = int(rows[i + 1]['dt'].timestamp() * 1000) if i + 1 < len(rows) else ini + 2 * 3600_000
            out.append({'tid': tid, 'ini': ini, 'fin': fin, 'titulo': r['titulo'],
                        'ep': '', 'genero': r['genero'], 'pais': ''})
    return out


# ------------------------------------------------------------------ XMLTV
def fxml(ms):
    when = datetime.fromtimestamp(ms / 1000, MAD)
    return when.strftime('%Y%m%d%H%M%S'), when.strftime('%z')


def clean(text):
    return ''.join(ch for ch in text if ord(ch) >= 32)[:500].strip()


def xmltv(canales, programas):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
             '<tv source-info-name="Movistar EPG (ottcache.dof6.com + movistarplus.es)">']
    for tid, name, logo in canales:
        lines.append('  <channel id="' + sax.escape(tid, {'"': '&quot;'}) + '">')
        lines.append('    <display-name lang="es">' + sax.escape(name) + '</display-name>')
        if logo:
            lines.append('    <icon src="' + sax.escape(logo, {'"': '&quot;'}) + '"/>')
        lines.append('  </channel>')
    n = 0
    for p in programas:
        slot, off = fxml(p['ini'])
        stop, off_stop = fxml(p['fin'])
        lines.append(f'  <programme start="{slot} {off}" stop="{stop} {off_stop}" '
                     f'channel="{sax.escape(p["tid"], {'"': '&quot;'})}">')
        lines.append('    <title lang="es">' + sax.escape(clean(p['titulo']) or 'Programa') + '</title>')
        if p.get('ep'):
            lines.append('    <sub-title lang="es">' + sax.escape(clean(p['ep'])) + '</sub-title>')
        if p.get('genero'):
            cats = [c.strip() for c in re.split(r'[<;]', p['genero']) if c.strip()] or [p['genero']]
            for cat in cats:
                lines.append('    <category lang="es">' + sax.escape(clean(cat)) + '</category>')
        if p.get('pais'):
            lines.append('    <country>' + sax.escape(clean(p['pais'])) + '</country>')
        lines.append('  </programme>')
        n += 1
    lines.append('</tv>')
    return '\n'.join(lines) + '\n', n


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description='EPG XMLTV de Movistar+ (1/2/7 dias)')
    ap.add_argument('--m3u', required=True, help='lista M3U con tvg-id/tvg-logo')
    ap.add_argument('--out', default='.', help='directorio de salida')
    ap.add_argument('--days', type=int, nargs='+', default=[1, 2, 7], help='ventanas en dias')
    ap.add_argument('--bind', default='', help='IP de salida (opcional)')
    args = ap.parse_args()

    canales = canales_de_m3u(args.m3u)
    tids = list(dict.fromkeys(c[0] for c in canales))
    dias_max = max(args.days)
    print(f'{len(canales)} canales del m3u ({len(tids)} unicos); ventana {dias_max} dias')

    passes, failed, desde = epg_api(tids, dias_max, bind=args.bind)
    print(f'API rejilla: {len(passes)} pases')

    con_api = {p['tid'] for p in passes}
    sin_api = [t for t in tids if t not in con_api]
    if sin_api:
        print(f'sin API, web ({len(sin_api)}): {" ".join(sin_api)}')
        passes += rellena_web(sin_api, dias_max, desde, bind=args.bind)
    # dedupe global
    seen, uniq = set(), []
    for p in passes:
        if not p['tid'] or not p['titulo']:
            continue
        k = (p['tid'], p['ini'])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    uniq.sort(key=lambda p: (p['tid'], p['ini']))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for dias in sorted(set(args.days)):
        limite = int((desde + timedelta(days=dias)).timestamp() * 1000)
        inicio = int(desde.timestamp() * 1000)
        ventana = [p for p in uniq if p['ini'] < limite and p['fin'] > inicio]
        xml, nprog = xmltv(canales, ventana)
        gz = out / f'epg{dias}.xml.gz'
        with gzip.open(gz, 'wt', encoding='utf-8') as fh:
            fh.write(xml)
        (out / f'epg{dias}.xml').write_text(xml, encoding='utf-8')
        print(f'epg{dias}: {nprog} programas -> {gz}')

    porcanal = {p['tid'] for p in uniq}
    print(f'con guia: {len(porcanal)} | sin guia: '
          f'{[t for t in tids if t not in porcanal] or "ninguno"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
