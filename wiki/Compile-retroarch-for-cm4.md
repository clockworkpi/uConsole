# Prepare

## In DevTerm or uConsole

uncomment deb-src in /etc/apt/source.list ,and run 'sudo apt update'

```
sudo apt install git build-essential libasound2-dev libudev-dev
sudo apt install -y libegl-mesa0
sudo apt-get build-dep retroarch
```

```
git clone https://github.com/libretro/RetroArch.git retroarch 
```

```
cd retroarch
./configure --disable-videocore --disable-vg --enable-alsa --enable-udev --disable-opengl1
make
sudo make install
```

```
mkdir -p /usr/local/bin 2>/dev/null || /bin/true
mkdir -p /etc 2>/dev/null || /bin/true
mkdir -p /usr/local/share/applications 2>/dev/null || /bin/true
mkdir -p /usr/local/share/metainfo 2>/dev/null || /bin/true
mkdir -p /usr/local/share/doc/retroarch 2>/dev/null || /bin/true
mkdir -p /usr/local/share/man/man6 2>/dev/null || /bin/true
mkdir -p /usr/local/share/pixmaps 2>/dev/null || /bin/true
cp retroarch /usr/local/bin
cp tools/cg2glsl.py /usr/local/bin/retroarch-cg2glsl
cp retroarch.cfg /etc
cp com.libretro.RetroArch.appdata.xml /usr/local/share/metainfo
cp retroarch.desktop /usr/local/share/applications
cp docs/retroarch.6 /usr/local/share/man/man6
cp docs/retroarch-cg2glsl.6 /usr/local/share/man/man6
cp media/retroarch.svg /usr/local/share/pixmaps
cp COPYING /usr/local/share/doc/retroarch
cp README.md /usr/local/share/doc/retroarch


chmod 755 /usr/local/bin/retroarch
chmod 755 /usr/local/bin/retroarch-cg2glsl
chmod 644 /etc/retroarch.cfg
chmod 644 /usr/local/share/applications/retroarch.desktop
chmod 644 /usr/local/share/metainfo/com.libretro.RetroArch.appdata.xml
chmod 644 /usr/local/share/man/man6/retroarch.6
chmod 644 /usr/local/share/man/man6/retroarch-cg2glsl.6
chmod 644 /usr/local/share/pixmaps/retroarch.svg

```

# Modify the RetroArch configuration and download the corresponding core file, taking the Nestopia simulator as an example
```
sudo apt-get install wget unzip -y 
sed -i '/^libretro_directory/c\libretro_directory = "~/.config/retroarch/cores"' ~/.config/retroarch/retroarch.cfg
wget https://github.com/christianhaitian/retroarch-cores/raw/master/aarch64/nestopia_libretro.so.zip && unzip nestopia_libretro.so.zip -d ~/.config/retroarch/cores && rm nestopia_libretro.so.zip 
```

# apt upgrade stock retroarch for DevTerm / uConsole
```
sudo apt update 
sudo apt install -y retroarch-cpi-cm4
```
Current retroarch
Version: 1.17.0 (Git 076d3cc91f)

# RetroArch CM4 64bit core download location

https://github.com/christianhaitian/retroarch-cores
