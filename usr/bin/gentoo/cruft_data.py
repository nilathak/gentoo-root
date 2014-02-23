    # helpers
    # =================================================
    def users():
        return self.dispatch('awk -F: \'{ printf $1 "," }\' /etc/passwd',
                             output=None).stdout[0].rstrip(',').split(',')
    def interfaces():
        return self.dispatch('for service in /etc/init.d/net.*; do echo -n ${service##*.},; done',
                             output=None).stdout[0].rstrip(',').split(',')
    def grab_setting(v):
        return self.dispatch('[[ -r /etc/make.globals ]] && source /etc/make.globals; \
        [[ -r ${PORTDIR-/usr/portage}/profiles/base/make.defaults ]] && source ${PORTDIR-/usr/portage}/profiles/base/make.defaults; \
        [[ -r /etc/make.profile/make.defaults ]] && source /etc/make.profile/make.defaults; \
        [[ -r /etc/make.conf ]] && source /etc/make.conf; \
        echo ${%s}' % v, output=None).stdout

    # conditions
    # =================================================
    def installed(v, slot=None, use=None):
        import portage
        return len(portage.db['/']['vartree'].dbapi.match(v)) > 0

    # Things belonging to users
    ignore(''.join(['^/var/spool/cron/crontabs/' + u for u in users()]))
    ignore(''.join(['^/var/spool/mail/' + u for u in users()]))

    # Local files
    ignore('''
    ^/etc/init\.d/local
    ^/usr/local
    ^/usr/share/applications/local
    ^/usr/share/control-center-2\.0/capplets/local
    ^/usr/share/faces
    ^/usr/share/fonts/local
    ^/usr/share/pixmaps/local
    ^/usr/share/sounds/local
    ''')

    # Admin-managed data and resources (files needed to regenerate a system)
    ignore('''
	^/etc/cron\.(hourly|daily|weekly|monthly|allow|deny)
	^/etc/dnsdomainname
	^/etc/fstab
	^/etc/group
	^/etc/group-
	^/etc/gshadow
	^/etc/gshadow-
	^/etc/hostname
	^/etc/hosts(\.(allow|deny|equiv))*
	^/etc/issue(\.net)*
	^/etc/make\.(conf|profile)
	^/etc/localtime
	^/etc/motd
	^/etc/passwd
	^/etc/passwd-
	^/etc/portage
	^/etc/runlevels
	^/etc/shadow
	^/etc/shadow-
	^/etc/skel
	^/etc/xprofile
	^/var/lib/portage
    ''')

    # Kernel and console
    ignore('''
    ^/etc/mtab
    ^/var/log/dmesg
    ^/var/run/console/console\.lock
    ''')
    ignore(''.join(['^/var/run/console/' + u for u in users()]))


    # java dependant ignores
    if java_installed():
        ignore('^/etc/\.java')
    if (java_installed('sun') or
        java_installed('ibm-jdk')):
        ignore(''.join(nsplugin('libjavaplugin_oji.so')))

    # ignores depending on certain files
    if exists('/var/lib/init.d/started/clock'):
        ignore('^/etc/adjtime')
    if exists('/var/lib/init.d/started/hostname'):
        ignore('^/etc/env\.d/01hostname')
    if (exists('/var/lib/init.d/started/bootmisc') or
        exists('/var/lib/init.d/started/domainname')):
        ignore('^/etc/resolv\.conf')
    if exists('/var/lib/init.d/started/urandom'):
        ignore('^/var/run/random-seed')

    # ignores depending on eclasses
    if eclass('games'):
        ignore('^/etc/env\.d/90games')
    if eclass('linux-mod'):
        ignore('^/usr/share/module-rebuild')

    # Package dependant ignores
    if installed('x11-base/xorg-x11'):
        ignore('/\.fonts\.cache-1')
    if installed('sys-apps/hal'):
        ignore('^/etc/\.fstab\.hal\.(1|i)')
    if installed('sys-apps/shadow'):
        ignore('^/etc/\.pwd\.lock')
    if installed('media-libs/alsa-lib'):
        ignore('^/etc/asound\.state')
    if installed('app-shells/bash'):
        ignore('^/etc/bash/bash_logout')
    if installed('sys-fs/e2fsprogs'):
        ignore('^/etc/blkid\.tab(\.old)*')
    if installed('net-mail/courier-imap'):
        ignore('^/etc/courier-imap/(authdaemond\.conf|imapd\.pem)')
    if installed('net-dns/ddclient'):
        ignore('^/etc/ddclient/ddclient\.(cache|conf)')
    if installed('app-misc/fdutils'):
        ignore('^/etc/driveprm')
    if installed('dev-java/java-config'):
        ignore('^/etc/env\.d/20java')
    if installed('net-fs/nfs-utils'):
        ignore('^/etc/exports')
    if installed('net-print/foomatic'):
        ignore('^/etc/foomatic\.cups')
    if installed('app-portage/gentoolkit-dev'):
        ignore('^/etc/gensync/.*\.syncsource')
    if installed('x11-libs/gtk+'):
        ignore('''
        ^/etc/gtk-2\.0/gtk\.immodules
        ^/etc/gtk-2\.0/gdk-pixbuf\.loaders
        ''')
    if installed('media-libs/libgphoto2'):
        ignore('^/etc/hotplug/usb/usbcam-gphoto2\.usermap')
    if installed('sys-apps/sysvinit'):
        ignore('^/etc/ioctl\.save')
    if installed('net-nds/openldap'):
        ignore('^/etc/openldap/ssl/ldap\.pem')
    if installed('dev-util/pretrace'):
        ignore('^/etc/pretrace\.conf')
    if installed('net-ftp/proftpd'):
        ignore('^/etc/proftpd/proftpd\.conf')
    if installed('x11-libs/pango'):
        ignore('^/etc/pango/pango\.modules')
    if installed('net-misc/rsync'):
        ignore('^/etc/rsync/rsyncd\.conf')
    if installed('app-text/docbook-dsssl-stylesheets'):
        ignore('^/etc/sgml/dsssl-docbook-stylesheets\.cat')
    if installed('app-text/docbook-xml-dtd'):
        ignore('^/etc/xml/docbook')
    if installed('sys-devel/gcc-config'):
        ignore('^/(usr/bin/(gcc-config|(${CHOST}-)*(gcc|cpp|cc|c\+\+|g\+\+|f77|g77|gcj)(32|64)*)|lib/(cpp|libgcc_s\.so(\.1)*))')
    if installed('media-gfx/gimp'):
        ignore('^/usr/bin/gimp')
    if installed('dev-lang/python'):
        ignore('''
        ^/usr/bin/python(2)*
        ^/var/log/python-updater\.log
        ''')
    if installed('net-www/netscape-flash'):
        ignore(''.join(nsplugin('libflashplayer.so')))
        ignore(''.join(nsplugin('flashplayer.xpt')))
    if installed('media-video/vlc'):
        ignore(''.join(nsplugin('libvlcplugin.so')))
    if installed('=dev-lang/python-2.3*'): VERSIONS !!!!!!!!!!!!!
        ignore('^/usr/lib/python2\.3/lib-dynload/bz2\.so')
    if installed('dev-python/pygtk'):
        ignore('^/usr/lib/python${PYVER}/site-packages/pygtk\.(py|pyc|pyo|pth)')
    if installed('media-libs/fontconfig'):
        ignore('^/usr/share/fonts/afms')
    if installed('media-fonts/urw-fonts'):
        ignore(''.join(fontdir('urw-fonts')))
    if installed('sys-apps/man'):
        ignore('^/usr/share/man/whatis')
    if installed('media-gfx/gimp'):
        ignore('^/usr/share/pixmaps/wilber-icon\.png')
    if installed('app-admin/gnome-system-tools'):
        ignore('^/var/cache/setup-tool-backends')
    if installed('net-firewall/iptables'):
        ignore('^/var/lib/iptables/rules-save')
    if installed('net-fs/nfs-utils'):
        ignore('^/var/lib/nfs/(e|rm|x)tab')
    if installed('sys-apps/dbus'):
        ignore('^/var/lib/dbus/(pid|system_bus_socket)')
    if installed('sys-apps/slocate'):
        ignore('^/var/lib/slocate/slocate\.db')
    if installed('sys-apps/pam-login'):
        ignore('^/var/log/lastlog')
    if installed('app-admin/sudo'):
        ignore('^/var/run/sudo')
    if installed('sys-process/cronbase'):
        ignore('^/var/spool/cron/lastrun/(cron\.(hourly|daily|weekly|monthly)|lock)')
    if (installed('net-misc/ssh') or
        installed('net-misc/openssh')):
        ignore('^/etc/ssh/ssh_host_(dsa_|rsa_)*key(\.pub)*')
    if installed('app-emulation/vmware-workstation'):
        ignore('''
        ^/etc/vmware
        ^/var/lock/subsys/vmware
        ^/var/run/vmware
        ''')
    if installed('app-text/docbook-sgml-dtd'):
        ignore('''
        ^/etc/sgml/sgml\.cenv
        ^/etc/sgml/sgml\.env
        ''')
        # cat /var/db/pkg/app-text/docbook-sgml-dtd-*/SLOT | sed 's:^:/etc/sgml/sgml-docbook-:; s:$:.cat:'
    if installed('app-text/sgml-common'):
        ignore('''
        ^/etc/sgml/sgml-ent\.cat
        ^/etc/sgml/sgml-docbook\.cat
        ^/etc/sgml/catalog
        ''')
    if installed('app-text/sgmltools-lite'):
        ignore('''
        ^/etc/env\.d/93sgmltools-lite
        ^/etc/sgml/sgml-lite\.cat
        ''')
    if installed('dev-db/mysql'):
        ignore('''
        ^/var/lib/mysql
        ^/var/log/mysql
        ^/var/run/mysqld
        ''')
    if installed('dev-lang/perl'):
        ignore('''
        ^/usr/lib/libperl\.so
        ''')
        # perl -e 'use Config; print $Config{installsitearch};'
        # perl -e 'use Config; print $Config{privlib}."/CPAN/Config.pm";'
        # perl -e 'use Config; print $Config{installarchlib}."/perllocal.pod";'
    if installed('dev-ruby/ruby-config'):
        ignore('''
        ^/usr/lib/libruby\.so
        ^/usr/share/man/man1/ruby\.1\.gz
        ^/usr/bin/(ruby|irb|erb|testrb|rdoc)
        ''')
    if installed('dev-util/ccache'):
        ignore('''
        ^/usr/lib/ccache/bin/(c\+\+|cc|g\+\+|gcc|${CHOST}-(c\+\+|g\+\+|gcc))
        ^${CCACHE_DIR}
        ''')
    if installed('kde-base/kdebase'):
        ignore('''
        ^/usr/kde/3\.2/share/templates/\.source/emptydir
        ^/var/log/kdm\.log
        ^/var/run/kdm\.pid
        ''')
        ignore(''.join(['^/var/tmp/kdecache-' + u for u in users()]))
    if installed('mail-mta/postfix'):
        ignore('''
        ^/etc/mail/aliases\.db
        ^/var/spool/postfix
        ''')
    if installed('media-gfx/xloadimage'):
        ignore('''
        ^/usr/bin/xview
        ^/usr/bin/xsetbg
        ^/usr/share/man/man1/xview\.1\.gz
        ^/usr/share/man/man1/xsetbg\.1\.gz
        ''')
    if installed('net-fs/nfs-utils'):
        ignore('''
        ^/var/run/rpc\.statd\.pid
        ^/var/lib/nfs/(sm|sm\.bak|state)
        ''')
    if installed('net-fs/samba'):
        ignore('''
        ^/etc/samba/smb\.conf
        ^/etc/samba/private
        ^/var/spool/samba
        ^/var/log/samba
        ^/var/log/samba3
        ^/var/run/samba
        ^/var/cache/samba
        ^/var/lib/samba
        ''')
    if installed('net-misc/dhcpcd'):
        ignore('''
        ^/etc/ntp\.conf
        ^/etc/ntp\.conf.sv
        ^/etc/resolv\.conf
        ^/etc/resolv\.conf\.sv
        ^/etc/yp\.conf
        ^/etc/yp\.conf\.sv
        ''')
        ignore(''.join(['^/var/cache/dhcpcd-%s\.cache' % i for i in interfaces()]))
        ignore(''.join(['^/var/lib/dhcpc(/dhcpcd(\.exe|-%s\.info(\.old)*))*' % i for i in interfaces()]))
        ignore(''.join(['^/var/run/dhcpcd-%s\.pid' % i for i in interfaces()]))
    if installed('net-misc/ntp'):
        ignore('''
        ^/etc/ntp\.conf
        ^/var/log/ntp\.log
        ''')
    if installed('net-misc/nxserver-freenx'):
        ignore('''
        ^/etc/env\.d/50nxserver
        ^/usr/NX/home/nx/\.ssh/known_hosts
        ^/usr/NX/var/db/closed
        ^/usr/NX/var/db/failed
        ^/usr/NX/var/db/running
        ''')
    if installed('net-misc/openssh'):
        ignore('^/etc/ssh/(moduli|ssh_config|sshd_config)')
    if installed('net-print/cups'):
        ignore('''
        ^/etc/cups
        ^/etc/printcap
        ^/var/log/cups
        ^/var/spool/cups
        ''')
    if installed('=net-www/apache-2*'):
        ignore('''
        ^/var/lib/dav
        ^/var/log/apache2
        ^/var/cache/apache2
        ^/etc/apache2/(conf/(ssl|vhosts)|(extra)*modules|lib|logs)
        ''')
    if installed('sys-apps/acpid'):
        ignore('''
        ^/var/log/acpid
        ^/var/run/acpid\.socket
        ''')
    if installed('sys-apps/baselayout'):
        ignore('''
        ^/etc/env\.d/02locale
        ^/etc/gentoo-release
        ^/etc/modprobe\.conf
        ^/etc/modprobe\.conf\.old
        ^/etc/modprobe\.devfs
        ^/etc/modprobe\.devfs\.old
        ^/etc/modules\.conf
        ^/etc/modules\.conf\.old
        ^/etc/ld\.so\.conf
        ^/etc/prelink\.conf
        ^/etc/profile\.env
        ^/etc/csh\.env
        ^/usr/share/man/\.keep\.gz
        ^/var/lib/init\.d
        ''')
    if installed('sys-apps/portage'):
        ignore('''
        ^${PORTDIR}
        ^/var/cache/edb
        ^/var/db/pkg
        ^/var/log/emerge\.log
        ^/var/log/emerge_fix-db\.log
        ^${PORT_LOGDIR}
        ^${PORTAGE_TMPDIR}/portage
        ''')
