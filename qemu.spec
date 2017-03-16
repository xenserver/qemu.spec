Summary: qemu-dm device model
Name: qemu
Version: 2.8.0
Release: 1.0.1
License: GPL
Source0: https://code.citrite.net/rest/archive/latest/projects/XSU/repos/%{name}/archive?at=v%{version}&format=tar.gz#/%{name}.tar.gz
Source1: qemu-wrapper
Source2: qemu_trad_image.py
#Patch0: qemu-xen-development.patch
BuildRequires: pciutils-devel libaio-devel glib2-devel libuuid-devel lzo-devel
BuildRequires: cyrus-sasl-devel gnutls-devel libcap-devel libjpeg-devel libpng-devel pixman-devel
BuildRequires: xen-dom0-devel xen-libs-devel

%description
This package contains Qemu.

%prep
%autosetup -p1

%build
./configure --cc=gcc --enable-xen --target-list=i386-softmmu --source-path=. \
	--prefix=%{_prefix} --bindir=%{_libdir}/xen/bin --datadir=%{_datarootdir}/qemu \
	--localstatedir=%{_localstatedir} --libexecdir=%{_libexecdir} --sysconfdir=%{_sysconfdir} \
	--disable-kvm --disable-docs --disable-guest-agent --disable-sdl \
	--disable-curses --disable-curl --disable-gtk --disable-bzip2 \
	--disable-strip --disable-gnutls
%{?cov_wrap} %{__make} %{?_smp_mflags} all 

%install
mkdir -p %{buildroot}%{_libdir}/xen/bin

rm -rf %{buildroot}
%{__make} %{?_smp_mflags} install DESTDIR=%{buildroot}
rm -rf %{buildroot}/usr/include %{buildroot}%{_libdir}/pkgconfig %{buildroot}%{_libdir}/libcacard.*a \
       %{buildroot}/usr/share/locale
%{__install} -D -m 775 %{SOURCE1} %{buildroot}%{_libdir}/xen/bin/qemu-wrapper
%{__install} -D -m 644 %{SOURCE2} %{buildroot}%{_libdir}/xen/bin/qemu_trad_image.py

%files
%{_libdir}/xen/bin
%{_datarootdir}/qemu
%{_libexecdir}/*

%changelog
* Tue Jun 16 2015 Ross Lagerwall <ross.lagerwall@citrix.com>
- Update for Xen 4.5.

* Tue Apr 8 2014 Frediano Ziglio <frediano.ziglio@citrix.com>
- First packaging

