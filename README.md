#  Smart Temperature Control System
**IoT & Cloud Computing**

---

##  Overview

An automated IoT temperature monitoring and control system built on a Raspberry Pi. The system reads real-time temperature and humidity data from a DHT11 sensor, automatically controls a cooling fan via smooth PWM, provides visual LED indicators, and streams all data to AWS IoT Core. A Flask-based web dashboard with Socket.IO provides live monitoring and remote control from any browser.

---

##  Features

- **Real-time Sensor Monitoring** – DHT11 temperature & humidity readings every 5 seconds
- **Smooth PWM Fan Control** – Gradual ramp-up/ramp-down prevents voltage spikes and Pi reboots
- **LED Visual Indicators** – Red LED blinks when temperature is high; Blue LED blinks when normal
- **AWS IoT Core Integration** – Publishes sensor data via MQTT; receives remote control commands from the cloud
- **Web Dashboard** – Real-time charts, historical data, interactive fan controls via browser
- **Secure Login** – Flask-Login authentication with hashed passwords (Werkzeug)
- **SQLite Local Storage** – All readings and settings persisted locally
- **Auto & Manual Modes** – Temperature-based automatic control or manual override

---

##  Hardware

| Component | GPIO (BCM) | Physical Pin |
|-----------|-----------|--------------|
| DHT11 Sensor | GPIO 27 | Pin 13 |
| Fan INA (PWM) | GPIO 5 | Pin 29 |
| Fan INB | GPIO 6 | Pin 31 |
| Red LED | GPIO 13 | Pin 33 |
| Blue LED | GPIO 19 | Pin 35 |

---

##  Project Structure

```
project/
├── App2.py              # Main application (Flask + GPIO + AWS IoT)
├── templates/
│   ├── index.html       # Web dashboard
│   └── login.html       # Login page
├── certs/               # AWS IoT certificates (not included – see setup)
│   ├── certificate.pem.crt
│   ├── private.pem.key
│   └── AmazonRootCA1.pem
├── temp_control.db      # SQLite database (auto-created on first run)
└── README.md
```

---

##  Installation & Setup

### 1. Prerequisites

- Raspberry Pi (any model with GPIO) running Raspberry Pi OS
- Python 3.7+
- Internet connection for AWS IoT

### 2. Install Dependencies

```bash
pip install flask flask-socketio flask-cors flask-login werkzeug \
            adafruit-circuitpython-dht awscrt awsiot
sudo apt-get install libgpiod2
```

### 3. Clone / Copy Project Files

Place `App2.py` and the `templates/` folder (containing `index.html` and `login.html`) in your project directory.

### 4. Configure AWS IoT

1. In the AWS Console, create an IoT Thing named `RaspberryPi_TempControl`.
2. Create and download certificates; place them in `/home/ruru/certs/`:
   - `certificate.pem.crt`
   - `private.pem.key`
   - `AmazonRootCA1.pem`
3. Attach an IoT Policy that allows `iot:Publish` on `temp/data` and `iot:Subscribe` on `temp/control`.
4. Update `AWS_IOT_ENDPOINT` in `App2.py` with your endpoint (found in AWS IoT → Settings).

> To disable AWS and run locally only, set `AWS_ENABLED = False` in `App2.py`.

### 5. Run the Application

```bash
python3 App2.py
```

You will see a menu:

```
1.   Start Console Control
2.  Start Web Dashboard
3.  Exit
```

Select **2** for the web dashboard, then open a browser and navigate to:

```
http://<raspberry-pi-ip>:5000
```

---

##  Login Credentials

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `iot2026` |

>  Change the default password before deploying in a production environment by updating `DEFAULT_PASSWORD` in `App2.py`.

---

##  Web API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Dashboard (login required) |
| GET/POST | `/login` | Login page |
| GET | `/logout` | Logout |
| GET | `/api/current` | Current temperature, humidity, fan status |
| GET | `/api/history?hours=1` | Historical readings |
| GET/POST | `/api/thresholds` | Get or update temperature thresholds |
| POST | `/api/fan` | Fan control (`on`, `off`, `auto`, `speed`) |

### Fan Control Examples

```bash
# Turn fan on
curl -X POST http://localhost:5000/api/fan -H "Content-Type: application/json" \
     -d '{"action": "on"}'

# Set fan to 60% speed
curl -X POST http://localhost:5000/api/fan -H "Content-Type: application/json" \
     -d '{"action": "speed", "speed": 60}'

# Switch to auto mode
curl -X POST http://localhost:5000/api/fan -H "Content-Type: application/json" \
     -d '{"action": "auto"}'
```

---

## 📡 AWS IoT MQTT Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `temp/data` | Pi → Cloud | Publishes temperature, humidity, fan status every 5 seconds |
| `temp/control` | Cloud → Pi | Receives remote commands |

### Supported Cloud Commands (via `temp/control`)

```json
{ "command": "fan_on" }
{ "command": "fan_off" }
{ "command": "auto_mode" }
{ "command": "set_threshold", "threshold": 30.0 }
```

---

##  Temperature Thresholds

| Setting | Default | Description |
|---------|---------|-------------|
| `temp_high` | 28.0°C | Fan turns ON above this; Red LED blinks |
| `temp_low` | 25.0°C | Reference lower bound (configurable via UI) |

Thresholds can be updated live through the web dashboard or the `/api/thresholds` endpoint.

---

##  PWM Fan Settings

| Setting | Value | Description |
|---------|-------|-------------|
| PWM Frequency | 1000 Hz | Smooth motor control |
| Max Fan Speed | 40% | Safe limit for Raspberry Pi power supply |
| Ramp Step | 5% | Speed increment per step |
| Ramp Delay | 0.2s | Delay between each ramp step |

Smooth ramping prevents sudden voltage spikes that would cause the Raspberry Pi to reboot.

---

##  Database Schema

The SQLite database (`temp_control.db`) is auto-created on first run.

**`readings`** – Sensor data log
```sql
id, timestamp, temperature, humidity, fan_speed
```

**`settings`** – Temperature threshold configuration
```sql
key, value
```

**`users`** – Authentication
```sql
id, username, password_hash
```

---

##  Troubleshooting

| Problem | Solution |
|---------|----------|
| DHT11 read errors | Sensor needs ≥2 seconds between reads; retries automatically up to 5 times |
| Pi reboots when fan starts/stops | Ensure smooth PWM ramping is enabled (default); do not set `FAN_SPEED_HIGH` above 40% |
| AWS IoT connection fails | Check certificate paths and that your IoT policy allows publish/subscribe on the configured topics |
| Port 5000 in use | Change the `port` value in `socketio.run(...)` at the bottom of `App2.py` |
| GPIO warnings | Run with `sudo` or ensure no other process is using the GPIO pins |
| Web dashboard not loading | Ensure `templates/` folder is in the same directory as `App2.py` |

---

##  System Architecture

```
DHT11 Sensor
     │
     ▼
Raspberry Pi (App2.py)
     │
     ├── GPIO → Fan (PWM) + LEDs
     │
     ├── SQLite (local storage)
     │
     ├── Flask Web Server (:5000)
     │        └── Socket.IO (real-time updates)
     │
     └── MQTT (AWS IoT SDK)
              │
              ▼
        AWS IoT Core
        temp/data  ──► AWS Lambda / DynamoDB / CloudWatch
        temp/control ◄── Remote commands
```

---

##  Notes

- The application uses `threading` for concurrent sensor reading, LED blinking, and the web server.
- All authentication passwords are stored as bcrypt hashes via Werkzeug — plain-text passwords are never stored.
- The `SECRET_KEY` in `app.config` should be changed to a strong random value for production deployments.
- Monitor your AWS Billing Dashboard regularly; some services (DynamoDB, CloudWatch) may incur charges after free-tier limits.
