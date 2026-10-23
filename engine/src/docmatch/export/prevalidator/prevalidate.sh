#!/bin/sh
# prevalidate.sh <sha256> <rnc> <periodo> <606 file>
#
# Runs DGII's pre-validator once over one 606 and prints the harness's
# verdict block. `Pre_Validado.zip` is mounted read-only at /dgii; it is
# checked against <sha256> first, and a different file stops here with exit 64
# before anything of it is unpacked or run.
#
# The cabinet's offset inside the MSI and the names its three files carry in
# it hold only for the zip of that hash (MSI 2.6, exe 4.0.0.9), found by
# #217's research run; the MSI and its setup.exe are never run.
set -eu

zip=/dgii/Pre_Validado.zip
if ! echo "$1  $zip" | sha256sum --check --quiet --status -; then
  echo "Pre_Validado.zip does not have the pinned SHA-256 $1" >&2
  exit 64
fi

app="$HOME/app"
mkdir -p "$app"
unzip -p "$zip" Prevalidador/Setup_Pre-validado.msi | tail -c +562177 > "$HOME/payload.cab"
cabextract -q -d "$app" "$HOME/payload.cab"
mv "$app/_123D9538941D1BC29E210A86182C26D4" "$app/DGII.PreValidado.exe"
mv "$app/_EF4830F20919BD4493EBCB2157BB8672" "$app/ICSharpCode.SharpZipLib.dll"
mv "$app/_65B6A15785BA34D85883CEA8AF75B966" "$app/DGII.PreValidado.exe.config"

# Not exec: as PID 1, xvfb-run never sees Xvfb signal that it is ready.
xvfb-run --auto-servernum --server-args "-screen 0 1280x900x24" \
  mono /opt/harness/Harness.exe "$app" "$4" "$2" "$3"
