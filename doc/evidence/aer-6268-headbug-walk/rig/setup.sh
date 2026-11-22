#!/bin/bash
# AER-6268: build the private fixgw tree (the branch under test) for one run.
#   bash setup.sh <run dir name, e.g. run2> [<commit>]
set -eu
RUN=${1:?run dir}; REF=${2:-origin/aer-6268/headbug-step}
W=~/bench-scratch/aer6268; cd $W
PY=$HOME/pyefis-venv/bin/python
[ -d fgw ] || git clone -q --branch aer-6268/headbug-step https://github.com/billmallard/fix-gateway.git fgw
git -C fgw fetch -q origin aer-6268/headbug-step && git -C fgw checkout -q --detach "$REF"
echo "fgw $(git -C fgw log --oneline -1)"
$PY mkconfig_fgw.py $W/fgw/src/fixgw/config $HOME/makerplane/fixgw/config/connections/xplane.yaml $W/fcfg 10.110.10.167 49104 3594 $W/$RUN/raw.jsonl
mkdir -p $W/$RUN && cp $W/session.sh $W/$RUN/
$PY -m py_compile seq.py && echo seq ok
