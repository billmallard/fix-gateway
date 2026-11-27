# Tessera spike page render (AER-5754)

`spike_page.png` (1024x600, the panel's native size) was drawn by
Tessera's own `tests/native/render_screens.cpp` (real panel UI code + LVGL) from `spike_page.json`,
as the keys screen, profile `FIXGW`, page `SPIKE`, back 0.

Environment: Linux x86_64, no system C compiler, python3.13. The toolchain is zig's clang via pip.

## Versions

- tessera-deck: commit 5ab2486e01595c0049fabdc8d0433145654d970e
- LVGL: v9.6.0 (dependencies.lock: lvgl/lvgl 9.6.0~1)
- cJSON: DaveGamble/cJSON v1.7.19 (dependencies.lock: espressif/cjson 1.7.19~2)
- pip: ziglang 0.17.1 (clang 22.1.8), cmake 4.4.5, ninja 1.13.2, pillow 13.0.0
- CONFIG_CJSON_NESTING_LIMIT=16 comes from the repo's own sdkconfig.defaults (no change needed)

## Commands, in order

```sh
D=$PWD/tess1-render   # any empty scratch directory
mkdir -p $D
cd $D

# 0. The layout: the shipped page with sample values, from a fix-gateway checkout
#    (copy spike_page.json from this directory, or regenerate it):
#    PYTHONPATH=src python3 -c "from fixgw.plugins.tessera import protocol; from fixgw.plugins.tessera.page import Page; v={'BARO':29.92,'HEADBUG':275.0,'COURSE':93.0,'NAVSRC':2.0,'MAPRANGE':2}; print(protocol.layout_payload(Page.load('src/fixgw/config/tessera/page.yaml').buttons(lambda k: (v[k],False,False,False,False,False))))" > $D/spike_page.json

# 1. Source
curl -sSL -o tessera.tar.gz https://codeload.github.com/catorendal-a11y/tessera-deck/tar.gz/5ab2486e01595c0049fabdc8d0433145654d970e
tar xzf tessera.tar.gz
mv tessera-deck-5ab2486e01595c0049fabdc8d0433145654d970e tessera

# 2. managed_components (what ESP-IDF would normally download)
mkdir -p dl tessera/managed_components/espressif__cjson
curl -sSL -o dl/lvgl.tar.gz  https://codeload.github.com/lvgl/lvgl/tar.gz/refs/tags/v9.6.0
curl -sSL -o dl/cjson.tar.gz https://codeload.github.com/DaveGamble/cJSON/tar.gz/refs/tags/v1.7.19
(cd dl && tar xzf lvgl.tar.gz && tar xzf cjson.tar.gz)
mv dl/lvgl-9.6.0   tessera/managed_components/lvgl__lvgl
mv dl/cJSON-1.7.19 tessera/managed_components/espressif__cjson/cJSON

# 3. Tools
python3 -m pip install -q --target $D/py ziglang cmake ninja pillow

# 4. Compiler wrappers (CMake wants single executables; zig also provides ar/ranlib)
mkdir -p wrap
printf '#!/bin/sh\nexec %s/py/ziglang/zig cc -target x86_64-linux-gnu "$@"\n'  $D > wrap/cc
printf '#!/bin/sh\nexec %s/py/ziglang/zig c++ -target x86_64-linux-gnu "$@"\n' $D > wrap/c++
printf '#!/bin/sh\nexec %s/py/ziglang/zig ar "$@"\n'     $D > wrap/ar
printf '#!/bin/sh\nexec %s/py/ziglang/zig ranlib "$@"\n' $D > wrap/ranlib
chmod +x wrap/*

# 5. Configure + build with the repo's unmodified tests/native/CMakeLists.txt
#    (pass CMAKE_AR/RANLIB on the FIRST configure; otherwise CMake caches AR-NOTFOUND)
export PYTHONPATH=$D/py PATH=$D/py/bin:$PATH
cmake -S tessera/tests/native -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=$D/wrap/cc -DCMAKE_CXX_COMPILER=$D/wrap/c++ \
  -DCMAKE_AR=$D/wrap/ar -DCMAKE_RANLIB=$D/wrap/ranlib
ninja -C build -j8 render_screens

# 6. Manifest (same format tools/render_screens.py writes; tab separated:
#    screen <out.ppm> <layout.json> <PROFILE> <PAGE> <back 0/1>)
mkdir -p out
printf 'screen\t%s/out/spike_page.ppm\t%s/spike_page.json\tFIXGW\tSPIKE\t0\n' $D $D > out/manifest.txt
./build/render_screens out/manifest.txt     # exit 0, writes a 1024x600 P6 PPM

# 7. PPM -> PNG (as tools/render_screens.py does: Image.open(ppm).save(png))
python3 -c "
from PIL import Image
im = Image.open('out/spike_page.ppm'); im.save('spike_page.png')"
```

Compiler flags are those of the repo's CMakeLists (C11 / C++17, `LV_CONF_INCLUDE_SIMPLE=1`,
`LV_USE_LZ4_INTERNAL=1`, `tests/native/lv_conf.h`) plus CMake's Release `-O3 -DNDEBUG`.
The renderer's clock is fixed at 14:32 (render_screens.cpp).
