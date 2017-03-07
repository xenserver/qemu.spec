Summary: qemu-dm device model
Name: qemu-xen
Version: 4.7.0
Release: 4.36786
License: GPL
Source0: https://code.citrite.net/rest/archive/latest/projects/XSU/repos/%{name}/archive?at=qemu-xen-%{version}&format=tar.gz#/%{name}.tar.gz
#Patch0: qemu-xen-development.patch
BuildRequires: pciutils-devel libaio-devel glib2-devel libuuid-devel lzo-devel
BuildRequires: cyrus-sasl-devel gnutls-devel libcap-devel libjpeg-devel libpng-devel pixman-devel
BuildRequires: xen-dom0-devel xen-libs-devel

%description
This package contains qemu-xen, the Xen device model based on Qemu upstream.

%prep
%autosetup -p1

%build
./configure --cc=gcc --enable-xen --target-list=i386-softmmu --source-path=. \
	--prefix=%{_prefix} --bindir=%{_libdir}/xen/bin --datadir=%{_datarootdir}/qemu-xen \
	--localstatedir=%{_localstatedir} --libexecdir=%{_libexecdir} --sysconfdir=%{_sysconfdir} \
	--disable-kvm --disable-docs --disable-guest-agent --disable-sdl \
	--disable-smartcard-nss --disable-curses --disable-curl --disable-gtk --disable-bzip2 \
	--enable-trace-backends=stderr
%{?cov_wrap} %{__make} all

%install
rm -rf %{buildroot}
%{__make} install DESTDIR=%{buildroot}
rm -rf %{buildroot}/usr/include %{buildroot}%{_libdir}/pkgconfig %{buildroot}%{_libdir}/libcacard.*a \
       %{buildroot}/usr/share/locale
install mk/qemu-wrapper $RPM_BUILD_ROOT%{_libdir}/xen/bin/qemu-wrapper
install mk/qemu_trad_image.py $RPM_BUILD_ROOT%{_libdir}/xen/bin/qemu_trad_image.py

%files
%{_libdir}/xen/bin
%{_datarootdir}/qemu-xen
%{_libexecdir}/*

%changelog
* Tue Jun 16 2015 Ross Lagerwall <ross.lagerwall@citrix.com>
- Update for Xen 4.5.

* Tue Apr 8 2014 Frediano Ziglio <frediano.ziglio@citrix.com>
- First packaging

