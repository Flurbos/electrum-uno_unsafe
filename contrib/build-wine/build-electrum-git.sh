#!/bin/bash

# You probably need to update only this link
ELECTRUM_GIT_URL=git://github.com/flurbos/electrum-uno.git
BRANCH=master
NAME_ROOT=electrum-uno


# These settings probably don't need any change
export WINEPREFIX=/opt/wine64

PYHOME=c:/python27
PYTHON="wine $PYHOME/python.exe -OO -B"


# Let's begin!
cd `dirname $0`
set -e

cd tmp

if [ -d "electrum-git" ]; then
    # GIT repository found, update it
    echo "Pull"
    cd electrum-git
    git checkout master
    git pull
    cd ..
else
    # GIT repository not found, clone it
    echo "Clone"
    git clone -b $BRANCH $ELECTRUM_GIT_URL electrum-git
fi

cd electrum-git
VERSION=`git rev-parse HEAD | awk '{ print substr($1, 0, 11) }'`
echo "Last commit: $VERSION"

cd ..

rm -rf $WINEPREFIX/drive_c/electrum-uno
cp -r electrum-git $WINEPREFIX/drive_c/electrum-uno
cp electrum-git/LICENCE .

# add python packages (built with make_packages)
cp -r ../../../packages $WINEPREFIX/drive_c/electrum-uno/

# add locale dir
cp -r ../../../lib/locale $WINEPREFIX/drive_c/electrum-uno/lib/

# Build Qt resources
wine $WINEPREFIX/drive_c/Python27/Lib/site-packages/PyQt4/pyrcc4.exe C:/electrum-uno/icons.qrc -o C:/electrum-uno/lib/icons_rc.py
wine $WINEPREFIX/drive_c/Python27/Lib/site-packages/PyQt4/pyrcc4.exe C:/electrum-uno/icons.qrc -o C:/electrum-uno/gui/qt/icons_rc.py

cd ..

rm -rf dist/

# build standalone version
$PYTHON "C:/pyinstaller/pyinstaller.py" --noconfirm --ascii -w deterministic.spec

# build NSIS installer
wine "$WINEPREFIX/drive_c/Program Files (x86)/NSIS/makensis.exe" electrum.nsi

cd dist
mv electrum-uno.exe $NAME_ROOT-$VERSION.exe
mv electrum-uno-setup.exe $NAME_ROOT-$VERSION-setup.exe
mv electrum-uno $NAME_ROOT-$VERSION
zip -r $NAME_ROOT-$VERSION.zip $NAME_ROOT-$VERSION
cd ..

# build portable version
cp portable.patch $WINEPREFIX/drive_c/electrum-uno
pushd $WINEPREFIX/drive_c/electrum-uno
patch < portable.patch 
popd
$PYTHON "C:/pyinstaller/pyinstaller.py" --noconfirm --ascii -w deterministic.spec
cd dist
mv electrum-uno.exe $NAME_ROOT-$VERSION-portable.exe
cd ..

echo "Done."
