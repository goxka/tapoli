#!/bin/bash
# Publica Tapoli en el NAS y lo revisa. Uso: tools/deploy.sh
#  1. comprobaciones estáticas (Python y JavaScript)
#  2. construye la imagen (red host) y actualiza el stack de Portainer conservando sus variables
#  3. espera a que el NAS sirva la versión nueva
#  4. tools/verify.py: batería de pruebas contra el NAS
set -euo pipefail
cd "$(dirname "$0")/.."
# Ajustes de publicación en .env: PANEL_URL, PORTAINER_URL, PORTAINER_ENDPOINT, PORTAINER_STACK, PORTAINER_TOKEN_FILE
set -a; . ./.env; set +a
T=$(cat "${PORTAINER_TOKEN_FILE/#\~/$HOME}")
P=${PORTAINER_URL:-http://localhost:9000}/api
EP=${PORTAINER_ENDPOINT:-1}
STACK=${PORTAINER_STACK:?Falta PORTAINER_STACK en .env}
START=$(date +%s)

echo "① Comprobaciones · ② imagen · ③ stack · ④ espera · ⑤ revisión"
python3 -m flake8 --select=F,E9 app/tapoli
node --check app/tapoli/static/app.js
python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ('config/config.yaml','config/go2rtc.yaml','docker-compose.portainer.yml')]"
find app -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

tar -cf /tmp/tapoli-ctx.tar Dockerfile requirements.txt app
curl -s -N -X POST -H "X-API-Key: $T" -H "Content-Type: application/x-tar" --data-binary @/tmp/tapoli-ctx.tar \
  "$P/endpoints/$EP/docker/build?t=tapoli:latest&rm=1&forcerm=1&networkmode=host" > /tmp/tapoli-build.log
rm -f /tmp/tapoli-ctx.tar
grep -q "Successfully tagged tapoli:latest" /tmp/tapoli-build.log || { echo "❌ Falló la construcción"; tail -c 1500 /tmp/tapoli-build.log; exit 1; }

umask 077
curl -s -H "X-API-Key: $T" $P/stacks/$STACK | python3 -c "
import json,sys; d=json.load(sys.stdin)
print(json.dumps({'stackFileContent':open('docker-compose.portainer.yml').read(),'env':d['Env'],'prune':True,'pullImage':False}))" > /tmp/tapoli-upd.json
curl -s -X PUT -H "X-API-Key: $T" -H "Content-Type: application/json" --data @/tmp/tapoli-upd.json "$P/stacks/$STACK?endpointId=$EP" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('Name')=='tapoli' else print('❌',d) or 1)"
rm -f /tmp/tapoli-upd.json

WANT=$(python3 -c "import sys; sys.path.insert(0,'tools'); import verify; print(verify.local_version())")
OK=0
for i in $(seq 1 60); do
  GOT=$(curl -s -m3 "$PANEL_URL/api/status" | python3 -c "import json,sys; print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)
  [ "$GOT" = "$WANT" ] && OK=1 && break; sleep 2
done
[ $OK = 1 ] || { echo "❌ El NAS no sirve la versión nueva ($WANT) tras 2 min"; exit 1; }
for n in tapoli-analizador tapoli-web; do   # y que los contenedores se hayan reiniciado de verdad
  for i in $(seq 1 60); do
    ST=$(curl -s -H "X-API-Key: $T" "$P/endpoints/$EP/docker/containers/$n/json" | python3 -c "
import json,sys,datetime; d=json.load(sys.stdin); print(int(datetime.datetime.fromisoformat(d['State']['StartedAt'][:19]+'+00:00').timestamp()))")
    [ "$ST" -ge "$START" ] && break; sleep 2
  done
  [ "$ST" -ge "$START" ] || { echo "❌ $n no se ha reiniciado"; exit 1; }
done
sleep 20   # que el analizador conecte y la web pase el healthcheck

python3 tools/verify.py --since "$START"
