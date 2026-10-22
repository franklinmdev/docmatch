#!/bin/sh
# run.sh <rnc> <periodo> <file>...  : one form run per file, output in /out/<file>.*
Xvfb :9 -screen 0 1280x900x24 >/dev/null 2>&1 &
export DISPLAY=:9
sleep 2
mcs -nologo -r:System.Windows.Forms.dll -r:System.Drawing.dll -out:/tmp/Harness.exe /harness/Harness.cs || exit 1
rnc=$1; per=$2; shift 2
for f in "$@"; do
  rm -rf ~/app ~/app*Informacion.txt /out/ready; cp -r /app ~/app
  mono /tmp/Harness.exe ~/app /files/$f $rnc $per > /out/$f.log 2>&1 &
  pid=$!
  i=0; while [ ! -f /out/ready ] && [ $i -lt 150 ]; do sleep 1; i=$((i+1)); done
  sleep 1
  import -window root /out/$f.png
  for g in ~/app*Informacion.txt ~/app/Informacion.txt; do [ -f "$g" ] && { echo "== $g"; cat "$g"; }; done > /out/$f.info 2>&1
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
done
