#!/bin/bash
#habilitar HTTPS en Apache2
echo -e "/n"
sleep 2
a2enmod ssl
sleep 1
a2ensite default-ssl
sleep 2
systemctl restart apache2
sleep 2
python3 /home/sagemcom/script/app.py &
sleep 1
python3 /home/sagemcom/script/acs_server_v2.py &
sleep 1
python3 /home/sagemcom/script/cpe_sentinel.py &
sleep 1
python3 /home/sagemcom/script/compliance_lab_full.py &
sleep 1
python3 /home/sagemcom/script/sagemcom_portal.py &
sleep 1
sudo python3 /home/sagemcom/script/docsis_provisioner.py &
sleep 1
sudo python3 /home/sagemcom/script/acs_server_v2_cpe.py &
sleep 1
python3 /home/sagemcom/script/ssh_log.py &
sleep 1
cd /home/sagemcom/script
#nohup python3 trading_screener_sqlite_v2.py > /home/sagemcom/script/screener.log 2>&1 &
nohup python3 trading_screener_sqlite_v2_auto.py > /home/sagemcom/script/screener_auto.log 2>&1 &
sleep 1
#nohup python3 EURUSD.py > /home/sagemcom/script/EURUSD.log 2>&1 &
#sleep 1
shutdown -r 07:00 "El sistema se reiniciará por mantenimiento programado"



