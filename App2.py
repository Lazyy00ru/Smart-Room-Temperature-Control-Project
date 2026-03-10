#!/usr/bin/env python3
"""
AUTOMATIC TEMPERATURE CONTROL SYSTEM - SMOOTH PWM VERSION
Assignment 2 - IoT & Cloud Computing

Features:
- Smooth PWM fan speed control (prevents Pi reboot)
- Gradual speed ramp-up and ramp-down
- Console and Web Dashboard modes
- AWS IoT Cloud integration
- LED visual indicators
- Real-time monitoring and control

Hardware:
- DHT11: GPIO 27 (Pi Pin 13)
- Fan INA: GPIO 5 (Pin 29) - PWM controlled
- Fan INB: GPIO 6 (Pin 31)
- Red LED: GPIO 13 (Pin 33)
- Blue LED: GPIO 19 (Pin 35)

Author: Su Min Wai
Student ID: 24025201
Date: February 2026
"""

from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import threading
import time
import board
import adafruit_dht
import RPi.GPIO as GPIO
from datetime import datetime
import sqlite3
import json
from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

# ==================== CONFIGURATION ====================

# AWS IoT Configuration
AWS_IOT_ENDPOINT = "axgk98sup1ry8-ats.iot.ap-southeast-2.amazonaws.com"
AWS_IOT_CERT_PATH = "/home/ruru/certs/certificate.pem.crt"
AWS_IOT_KEY_PATH = "/home/ruru/certs/private.pem.key"
AWS_IOT_CA_PATH = "/home/ruru/certs/AmazonRootCA1.pem"
AWS_IOT_CLIENT_ID = "RaspberryPi_TempControl"
AWS_IOT_TOPIC_PUBLISH = "temp/data"
AWS_IOT_TOPIC_SUBSCRIBE = "temp/control"
AWS_ENABLED = True  # Cloud integration enabled

# Pin Configuration
FAN_INA = 5
FAN_INB = 6

# LED Pins
LED_RED = 13    # GPIO 13 (Pi Pin 33) - Red LED for high temp
LED_BLUE = 19   # GPIO 19 (Pi Pin 35) - Blue LED for normal temp

# PWM Configuration
PWM_FREQUENCY = 1000  # Hz

# Fan Speed Settings (0-100%)
FAN_SPEED_HIGH = 40    # Maximum speed when cooling (40% - SAFE for Pi power)
FAN_SPEED_LOW = 0      # Minimum speed (OFF)
SPEED_RAMP_STEP = 5    # Change speed by 5% per step
SPEED_RAMP_DELAY = 0.2 # Delay between steps (seconds)

# Temperature Thresholds
thresholds = {
    'temp_high': 28.0,  # Fan ON above this, OFF at or below this
    'temp_low': 25.0    # Not used in simple mode (kept for web interface)
}

UPDATE_INTERVAL = 5

# Flask Setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'iot-temp-control-secret-key-2026'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# User class for authentication
class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

# Default user credentials (stored in database)
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "iot2026"  # Change this for production!

# DHT Sensor
dht_device = None

# PWM object
fan_pwm = None

# System State
system_state = {
    'fan_running': False,
    'fan_speed': 0,  # Current PWM speed (0-100)
    'target_speed': 0,  # Target speed we're ramping to
    'auto_mode': True,
    'last_temp': None,
    'last_humidity': None,
    'last_update': None,
    'web_mode': False,
    'led_blink_active': True,  # Control LED blinking
    'aws_connected': False  # AWS IoT connection status
}

# AWS IoT Connection
mqtt_connection = None

# ==================== DATABASE ====================

def init_db():
    """Initialize SQLite database"""
    conn = sqlite3.connect('temp_control.db')
    c = conn.cursor()
    
    # Readings table
    c.execute('''CREATE TABLE IF NOT EXISTS readings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT NOT NULL,
                  temperature REAL,
                  humidity REAL,
                  fan_speed INTEGER)''')
    
    # Settings table
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY,
                  value REAL)''')
    
    # Users table for authentication
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL)''')
    
    # Insert default thresholds
    for key, value in thresholds.items():
        c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
                 (key, value))
    
    # Insert default user if not exists
    c.execute('SELECT COUNT(*) FROM users WHERE username = ?', (DEFAULT_USERNAME,))
    if c.fetchone()[0] == 0:
        password_hash = generate_password_hash(DEFAULT_PASSWORD)
        c.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                 (DEFAULT_USERNAME, password_hash))
        print(f"✓ Default user created: {DEFAULT_USERNAME}")
    
    conn.commit()
    conn.close()
    print("✓ Database initialized")

def save_reading(temp, humidity, fan_speed):
    """Save sensor reading"""
    try:
        conn = sqlite3.connect('temp_control.db')
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        c.execute('INSERT INTO readings (timestamp, temperature, humidity, fan_speed) VALUES (?, ?, ?, ?)',
                 (timestamp, temp, humidity, fan_speed))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def load_thresholds():
    """Load thresholds from database"""
    global thresholds
    try:
        conn = sqlite3.connect('temp_control.db')
        c = conn.cursor()
        c.execute('SELECT key, value FROM settings')
        rows = c.fetchall()
        for key, value in rows:
            thresholds[key] = value
        conn.close()
        print(f"✓ Thresholds: {thresholds['temp_low']}°C - {thresholds['temp_high']}°C")
    except:
        pass

# ==================== GPIO & PWM SETUP ====================

def setup_gpio():
    """Initialize GPIO and PWM"""
    global dht_device, fan_pwm
    
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(FAN_INA, GPIO.OUT)
    GPIO.setup(FAN_INB, GPIO.OUT)
    
    # Setup LED pins
    GPIO.setup(LED_RED, GPIO.OUT)
    GPIO.setup(LED_BLUE, GPIO.OUT)
    
    # Initialize PWM on INA pin
    fan_pwm = GPIO.PWM(FAN_INA, PWM_FREQUENCY)
    fan_pwm.start(0)  # Start at 0% duty cycle
    
    # INB always LOW for forward direction
    GPIO.output(FAN_INB, GPIO.LOW)
    
    # LEDs start OFF
    GPIO.output(LED_RED, GPIO.LOW)
    GPIO.output(LED_BLUE, GPIO.LOW)
    
    # Initialize DHT sensor (suppress libgpiod warnings)
    import sys
    import os
    
    # Temporarily redirect stderr to suppress warnings
    original_stderr = sys.stderr
    sys.stderr = open(os.devnull, 'w')
    
    try:
        dht_device = adafruit_dht.DHT11(board.D27)  # GPIO 27 (Pi Pin 13)
    finally:
        sys.stderr.close()
        sys.stderr = original_stderr
    
    print("✓ GPIO and PWM initialized")
    print(f"  PWM Frequency: {PWM_FREQUENCY} Hz")
    print(f"  Max Fan Speed: {FAN_SPEED_HIGH}%")
    print(f"  DHT11: GPIO 27 (Pin 13)")
    print(f"  Red LED: GPIO {LED_RED} (Pin 33)")
    print(f"  Blue LED: GPIO {LED_BLUE} (Pin 35)")

def cleanup_gpio():
    """Cleanup GPIO"""
    if fan_pwm:
        # Gradually stop fan before cleanup
        ramp_fan_speed(0)
        fan_pwm.stop()
    GPIO.output(FAN_INB, GPIO.LOW)
    
    # Turn off LEDs
    GPIO.output(LED_RED, GPIO.LOW)
    GPIO.output(LED_BLUE, GPIO.LOW)
    
    GPIO.cleanup()
    if dht_device:
        dht_device.exit()

# ==================== AWS IOT CLOUD ====================

def on_connection_interrupted(connection, error, **kwargs):
    """Callback when connection is interrupted"""
    print(f"⚠️  AWS IoT connection interrupted: {error}")
    system_state['aws_connected'] = False

def on_connection_resumed(connection, return_code, session_present, **kwargs):
    """Callback when connection resumes"""
    print(f"✓ AWS IoT connection resumed")
    system_state['aws_connected'] = True

def on_message_received(topic, payload, **kwargs):
    """Callback when message received from AWS IoT"""
    try:
        message = json.loads(payload)
        print(f"📩 AWS Message: {message}")
        
        # Handle remote control commands
        if 'command' in message:
            if message['command'] == 'fan_on':
                fan_on()
                system_state['auto_mode'] = False
                print("☁️  Cloud command: Fan ON")
            elif message['command'] == 'fan_off':
                fan_off()
                system_state['auto_mode'] = False
                print("☁️  Cloud command: Fan OFF")
            elif message['command'] == 'auto_mode':
                system_state['auto_mode'] = True
                print("☁️  Cloud command: Auto Mode")
            elif message['command'] == 'set_threshold':
                if 'threshold' in message:
                    thresholds['temp_high'] = float(message['threshold'])
                    print(f"☁️  Cloud command: Threshold set to {message['threshold']}°C")
    except Exception as e:
        print(f"❌ Error processing cloud message: {e}")

def connect_aws_iot():
    """Connect to AWS IoT Core"""
    global mqtt_connection
    
    if not AWS_ENABLED:
        print("ℹ️  AWS IoT disabled (set AWS_ENABLED=True to enable)")
        return False
    
    try:
        print("🔄 Connecting to AWS IoT Core...")
        
        # Create MQTT connection
        event_loop_group = io.EventLoopGroup(1)
        host_resolver = io.DefaultHostResolver(event_loop_group)
        client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)
        
        mqtt_connection = mqtt_connection_builder.mtls_from_path(
            endpoint=AWS_IOT_ENDPOINT,
            cert_filepath=AWS_IOT_CERT_PATH,
            pri_key_filepath=AWS_IOT_KEY_PATH,
            client_bootstrap=client_bootstrap,
            ca_filepath=AWS_IOT_CA_PATH,
            client_id=AWS_IOT_CLIENT_ID,
            clean_session=False,
            keep_alive_secs=30,
            on_connection_interrupted=on_connection_interrupted,
            on_connection_resumed=on_connection_resumed
        )
        
        # Connect
        connect_future = mqtt_connection.connect()
        connect_future.result()
        print(f"✓ Connected to AWS IoT: {AWS_IOT_ENDPOINT}")
        
        # Subscribe to control topic
        subscribe_future, packet_id = mqtt_connection.subscribe(
            topic=AWS_IOT_TOPIC_SUBSCRIBE,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_message_received
        )
        subscribe_result = subscribe_future.result()
        print(f"✓ Subscribed to topic: {AWS_IOT_TOPIC_SUBSCRIBE}")
        
        system_state['aws_connected'] = True
        return True
        
    except Exception as e:
        print(f"❌ AWS IoT connection failed: {e}")
        print("   Check credentials and endpoint configuration")
        return False

def publish_to_aws(data):
    """Publish sensor data to AWS IoT"""
    global mqtt_connection
    
    if not AWS_ENABLED or not system_state['aws_connected'] or mqtt_connection is None:
        return
    
    try:
        # Create message payload
        payload = {
            'device_id': AWS_IOT_CLIENT_ID,
            'timestamp': data['timestamp'],
            'temperature': data['temperature'],
            'humidity': data['humidity'],
            'fan_speed': data['fan_speed'],
            'fan_running': data['fan_running'],
            'auto_mode': data['auto_mode'],
            'led_red': data['temperature'] > thresholds['temp_high'],
            'led_blue': data['temperature'] <= thresholds['temp_high'],
            'threshold': thresholds['temp_high']
        }
        
        # Publish to AWS IoT
        mqtt_connection.publish(
            topic=AWS_IOT_TOPIC_PUBLISH,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE
        )
        
        # print(f"☁️  Published to AWS IoT")  # Uncomment for debug
        
    except Exception as e:
        print(f"❌ AWS publish error: {e}")

def disconnect_aws_iot():
    """Disconnect from AWS IoT"""
    global mqtt_connection
    
    if mqtt_connection and system_state['aws_connected']:
        try:
            print("🔄 Disconnecting from AWS IoT...")
            disconnect_future = mqtt_connection.disconnect()
            disconnect_future.result()
            print("✓ Disconnected from AWS IoT")
            system_state['aws_connected'] = False
        except:
            pass

# ==================== LED CONTROL ====================

led_state = {'current_color': None}  # Track which LED should blink

def led_blink_loop():
    """Background thread for LED blinking - ti ti ti effect!"""
    while system_state['led_blink_active']:
        try:
            if led_state['current_color'] == 'red':
                # Blink RED - ti ti ti
                GPIO.output(LED_RED, GPIO.HIGH)
                GPIO.output(LED_BLUE, GPIO.LOW)
                time.sleep(0.5)  # ON for 0.5s
                GPIO.output(LED_RED, GPIO.LOW)
                time.sleep(0.5)  # OFF for 0.5s
                
            elif led_state['current_color'] == 'blue':
                # Blink BLUE - ti ti ti
                GPIO.output(LED_BLUE, GPIO.HIGH)
                GPIO.output(LED_RED, GPIO.LOW)
                time.sleep(0.5)  # ON for 0.5s
                GPIO.output(LED_BLUE, GPIO.LOW)
                time.sleep(0.5)  # OFF for 0.5s
                
            else:
                # Both OFF
                GPIO.output(LED_RED, GPIO.LOW)
                GPIO.output(LED_BLUE, GPIO.LOW)
                time.sleep(0.5)
        except:
            time.sleep(0.5)

def update_leds(temperature):
    """Update which LED should blink based on temperature"""
    if temperature > thresholds['temp_high']:
        # High temperature - RED LED blinking
        led_state['current_color'] = 'red'
    else:
        # Normal temperature - BLUE LED blinking  
        led_state['current_color'] = 'blue'

# ==================== SMOOTH FAN CONTROL ====================

def ramp_fan_speed(target_speed):
    """
    Gradually change fan speed to prevent voltage spikes
    This prevents Pi from rebooting!
    """
    global fan_pwm
    
    current = system_state['fan_speed']
    system_state['target_speed'] = target_speed
    
    # Calculate direction
    if current < target_speed:
        # Ramping UP
        while current < target_speed:
            current = min(current + SPEED_RAMP_STEP, target_speed)
            fan_pwm.ChangeDutyCycle(current)
            system_state['fan_speed'] = current
            time.sleep(SPEED_RAMP_DELAY)
            print(f"  ↗ Ramping UP: {current}%")
    
    elif current > target_speed:
        # Ramping DOWN (critical for preventing reboot!)
        while current > target_speed:
            current = max(current - SPEED_RAMP_STEP, target_speed)
            fan_pwm.ChangeDutyCycle(current)
            system_state['fan_speed'] = current
            time.sleep(SPEED_RAMP_DELAY)
            print(f"  ↘ Ramping DOWN: {current}%")
    
    # Update fan running state
    system_state['fan_running'] = (target_speed > 0)

def fan_on():
    """Turn fan ON - ramp to 80% speed"""
    print(f"🌀 Fan: Ramping to {FAN_SPEED_HIGH}%")
    ramp_fan_speed(FAN_SPEED_HIGH)
    print(f"✓ Fan: Running at {FAN_SPEED_HIGH}%")

def fan_off():
    """Turn fan OFF - gradually slow down"""
    print("⭕ Fan: Ramping down to OFF")
    ramp_fan_speed(FAN_SPEED_LOW)
    print("✓ Fan: Stopped")

def set_fan_speed_direct(speed):
    """Set fan to specific speed with ramping"""
    ramp_fan_speed(speed)

# ==================== SENSOR READING ====================

def read_sensor():
    """Read DHT11 sensor with improved reliability"""
    global dht_device
    
    # DHT11 needs minimum 2 seconds between reads
    time.sleep(0.5)  # Small delay before reading
    
    max_retries = 5  # Increased from 3
    for attempt in range(max_retries):
        try:
            temperature = dht_device.temperature
            humidity = dht_device.humidity
            
            if temperature is not None and humidity is not None:
                return round(temperature, 1), round(humidity, 1)
            
            # Failed - wait longer before retry
            if attempt < max_retries - 1:
                time.sleep(2)  # DHT11 needs time to recover
                
        except RuntimeError as e:
            # Normal DHT11 timeout - wait and retry
            if attempt < max_retries - 1:
                time.sleep(2)
                
        except Exception as e:
            # Serious error - reinitialize sensor
            print(f"  ⚠️  Sensor error on attempt {attempt+1}, reinitializing...")
            if attempt < max_retries - 1:
                try:
                    if dht_device:
                        dht_device.exit()
                    time.sleep(2)  # Longer wait
                    
                    # Reinitialize with fresh GPIO
                    GPIO.cleanup(27)  # Clean up Pin 13 specifically
                    time.sleep(0.5)
                    
                    import sys, os
                    original_stderr = sys.stderr
                    sys.stderr = open(os.devnull, 'w')
                    try:
                        dht_device = adafruit_dht.DHT11(board.D27)
                    finally:
                        sys.stderr.close()
                        sys.stderr = original_stderr
                    
                    time.sleep(1)
                except Exception as reinit_error:
                    print(f"  ❌ Reinit failed: {reinit_error}")
    
    # All retries exhausted
    return None, None

# ==================== TEMPERATURE CONTROL ====================

def control_temperature(temp):
    """Simple automatic control logic - like Assignment 1"""
    if not system_state['auto_mode']:
        return
    
    current_running = system_state['fan_running']
    
    # Update LEDs based on temperature
    update_leds(temp)
    
    # Simple logic: ON if > 28°C, OFF if <= 28°C
    if temp > thresholds['temp_high'] and not current_running:
        # Temperature above 28°C - turn fan ON
        fan_on()
    elif temp <= thresholds['temp_high'] and current_running:
        # Temperature at or below 28°C - turn fan OFF
        fan_off()

# ==================== WEB DATA COLLECTION ====================

def web_data_collection_loop():
    """Data collection for web mode"""
    time.sleep(2)
    print("✓ Web data collection started")
    print("  Sensor on GPIO 27 (Pin 13)")
    print("  Reading every 5 seconds...")
    print()
    
    while system_state['web_mode']:
        try:
            temp, humidity = read_sensor()
            
            if temp is not None:
                system_state['last_temp'] = temp
                system_state['last_humidity'] = humidity
                system_state['last_update'] = datetime.now().isoformat()
                
                control_temperature(temp)
                save_reading(temp, humidity, system_state['fan_speed'])
                
                data = {
                    'temperature': temp,
                    'humidity': humidity,
                    'fan_running': system_state['fan_running'],
                    'fan_speed': system_state['fan_speed'],
                    'auto_mode': system_state['auto_mode'],
                    'temp_high': thresholds['temp_high'],
                    'temp_low': thresholds['temp_low'],
                    'timestamp': system_state['last_update']
                }
                
                socketio.emit('sensor_update', data)
                
                # Publish to AWS IoT Cloud
                publish_to_aws(data)
                
                fan_status = f"{system_state['fan_speed']}% 🌀" if system_state['fan_running'] else "OFF ⭕"
                mode = "AUTO" if system_state['auto_mode'] else "MANUAL"
                led_color = "🔴" if temp > thresholds['temp_high'] else "🔵"
                aws_status = "☁️" if system_state['aws_connected'] else ""
                print(f"[{mode}] T={temp}°C H={humidity}% Fan={fan_status} LED={led_color} {aws_status}")
            else:
                print("⚠️  Sensor read failed - retrying...")
            
            time.sleep(UPDATE_INTERVAL)
        except Exception as e:
            print(f"❌ ERROR in data collection: {e}")
            time.sleep(UPDATE_INTERVAL)

# ==================== CONSOLE MODE ====================

def test_fan_pwm():
    """Test PWM fan control"""
    print("\n" + "="*60)
    print("TEST: PWM Fan Control (Smooth Ramping)")
    print("="*60)
    
    print("\n1. Fan OFF")
    ramp_fan_speed(0)
    time.sleep(2)
    
    print("\n2. Ramp to 40%")
    ramp_fan_speed(40)
    time.sleep(3)
    
    print("\n3. Ramp to 80%")
    ramp_fan_speed(80)
    time.sleep(3)
    
    print("\n4. Ramp down to 0% (smooth stop)")
    ramp_fan_speed(0)
    time.sleep(2)
    
    print("\n✓ PWM test complete - No reboot!")
    return True

def run_console_tests():
    """Run hardware tests"""
    print("\n" + "="*60)
    print("🧪 HARDWARE TESTS")
    print("="*60)
    
    input("Press Enter to start...")
    setup_gpio()
    
    # Test DHT11
    print("\nTesting DHT11...")
    temp, hum = read_sensor()
    dht_ok = temp is not None
    if dht_ok:
        print(f"✓ DHT11: {temp}°C | {hum}%")
    else:
        print("✗ DHT11 failed")
    
    # Test Fan
    fan_ok = test_fan_pwm()
    
    print("\n" + "="*60)
    print("📊 RESULTS")
    print("="*60)
    print(f"DHT11 Sensor................. {'✓ PASS' if dht_ok else '✗ FAIL'}")
    print(f"PWM Fan Control.............. {'✓ PASS' if fan_ok else '✗ FAIL'}")
    print("="*60 + "\n")
    
    cleanup_gpio()

def run_console_control():
    """Console temperature control"""
    print("\n" + "="*60)
    print("🌡️  CONSOLE CONTROL - SMOOTH PWM + LED INDICATORS")
    print("="*60)
    print(f"Threshold: {thresholds['temp_high']}°C")
    print(f"  → Temp > 28°C: RED LED blinking 🔴")
    print(f"  → Temp ≤ 28°C: BLUE LED blinking 🔵")
    print("Press Ctrl+C to stop\n")
    
    setup_gpio()
    system_state['auto_mode'] = True
    system_state['led_blink_active'] = True
    
    # Start LED blinking thread
    led_thread = threading.Thread(target=led_blink_loop, daemon=True)
    led_thread.start()
    
    try:
        while True:
            temp, hum = read_sensor()
            
            if temp is not None:
                # Determine LED color
                led_color = "🔴" if temp > thresholds['temp_high'] else "🔵"
                
                print(f"🌡️  {temp}°C | 💧 {hum}% | Fan: {system_state['fan_speed']}%", end="")
                
                control_temperature(temp)
                
                if system_state['fan_running']:
                    print(f" 🌀 | LED: {led_color}")
                else:
                    print(f" ⭕ | LED: {led_color}")
                
                save_reading(temp, hum, system_state['fan_speed'])
            
            time.sleep(UPDATE_INTERVAL)
    
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        system_state['led_blink_active'] = False
        cleanup_gpio()

# ==================== AUTHENTICATION ====================

@login_manager.user_loader
def load_user(user_id):
    """Load user from database"""
    try:
        conn = sqlite3.connect('temp_control.db')
        c = conn.cursor()
        c.execute('SELECT id, username, password_hash FROM users WHERE id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        if row:
            return User(row[0], row[1], row[2])
    except:
        pass
    return None

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        try:
            conn = sqlite3.connect('temp_control.db')
            c = conn.cursor()
            c.execute('SELECT id, username, password_hash FROM users WHERE username = ?', (username,))
            row = c.fetchone()
            conn.close()
            
            if row and check_password_hash(row[2], password):
                user = User(row[0], row[1], row[2])
                login_user(user)
                return redirect(url_for('index'))
            else:
                flash('Invalid username or password', 'error')
        except Exception as e:
            flash('Login error occurred', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    return redirect(url_for('login'))

# ==================== WEB ROUTES ====================

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=current_user.username)

@app.route('/api/current')
@login_required
def get_current():
    return jsonify({
        'temperature': system_state['last_temp'],
        'humidity': system_state['last_humidity'],
        'fan_running': system_state['fan_running'],
        'fan_speed': system_state['fan_speed'],
        'auto_mode': system_state['auto_mode'],
        'temp_high': thresholds['temp_high'],
        'temp_low': thresholds['temp_low'],
        'timestamp': system_state['last_update']
    })

@app.route('/api/history')
@login_required
def get_history():
    try:
        hours = request.args.get('hours', default=1, type=int)
        conn = sqlite3.connect('temp_control.db')
        c = conn.cursor()
        c.execute('''SELECT timestamp, temperature, humidity, fan_speed 
                    FROM readings ORDER BY timestamp DESC LIMIT ?''', (hours * 720,))
        rows = c.fetchall()
        conn.close()
        
        data = [{'timestamp': r[0], 'temperature': r[1], 
                'humidity': r[2], 'fan_speed': r[3]} 
                for r in reversed(rows)]
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/thresholds', methods=['GET', 'POST'])
@login_required
def handle_thresholds():
    global thresholds
    
    if request.method == 'GET':
        return jsonify(thresholds)
    
    elif request.method == 'POST':
        try:
            new = request.json
            if new['temp_low'] >= new['temp_high']:
                return jsonify({'error': 'Invalid range'}), 400
            
            conn = sqlite3.connect('temp_control.db')
            c = conn.cursor()
            for key, value in new.items():
                c.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
            conn.commit()
            conn.close()
            
            thresholds.update(new)
            print(f"✓ Updated: {thresholds['temp_low']}°C - {thresholds['temp_high']}°C")
            
            # IMMEDIATELY check temperature with new threshold
            if system_state['last_temp'] is not None:
                control_temperature(system_state['last_temp'])
                print(f"  Re-evaluated with temp={system_state['last_temp']}°C")
            
            return jsonify({'success': True, 'thresholds': thresholds})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

@app.route('/api/fan', methods=['POST'])
@login_required
def control_fan():
    try:
        data = request.json
        action = data.get('action')
        
        if action == 'on':
            fan_on()
            system_state['auto_mode'] = False
            return jsonify({'success': True, 'fan_running': True, 
                          'fan_speed': system_state['fan_speed'], 'auto_mode': False})
        elif action == 'off':
            fan_off()
            system_state['auto_mode'] = False
            return jsonify({'success': True, 'fan_running': False, 
                          'fan_speed': 0, 'auto_mode': False})
        elif action == 'auto':
            system_state['auto_mode'] = True
            return jsonify({'success': True, 'auto_mode': True})
        elif action == 'speed':
            # Manual speed control
            speed = int(data.get('speed', FAN_SPEED_HIGH))
            speed = max(0, min(100, speed))  # Clamp 0-100
            set_fan_speed_direct(speed)
            system_state['auto_mode'] = False
            return jsonify({'success': True, 'fan_speed': speed, 'auto_mode': False})
        else:
            return jsonify({'error': 'Invalid action'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    print('✓ Web client connected')
    emit('connection_response', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    print('✗ Web client disconnected')

# ==================== MAIN MENU ====================

def show_main_menu():
    """Display main menu"""
    print("\n" + "="*60)
    print("🌡️  TEMPERATURE CONTROL - SMOOTH PWM VERSION")
    print("="*60)
    print(f"\n  Max Fan Speed: {FAN_SPEED_HIGH}%")
    print(f"  Ramp Step: {SPEED_RAMP_STEP}% every {SPEED_RAMP_DELAY}s")
    print(f"  (Prevents Pi reboot on sudden stops!)")
    print("\n1. 🖥️  Start Console Control")
    print("2. 🌐 Start Web Dashboard")
    print("3. ❌ Exit")
    print("\n" + "="*60)

def run_web_server():
    """Start web server mode"""
    print("\n" + "="*70)
    print("  🌐 WEB DASHBOARD + AWS IOT CLOUD")
    print("="*70)
    
    init_db()
    load_thresholds()
    setup_gpio()
    
    # Connect to AWS IoT
    if AWS_ENABLED:
        connect_aws_iot()
    
    system_state['web_mode'] = True
    system_state['led_blink_active'] = True
    
    # Start LED blinking thread
    led_thread = threading.Thread(target=led_blink_loop, daemon=True)
    led_thread.start()
    
    # Start data collection
    collection_thread = threading.Thread(target=web_data_collection_loop, daemon=True)
    collection_thread.start()
    
    print("✓ Server: http://localhost:5000")
    print("✓ LEDs blinking based on temperature!")
    if system_state['aws_connected']:
        print("✓ AWS IoT Cloud connected ☁️")
    print()
    
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        system_state['web_mode'] = False
        system_state['led_blink_active'] = False
        disconnect_aws_iot()
        cleanup_gpio()

def main():
    """Main program"""
    print("\n" + "="*60)
    print("  SMOOTH PWM TEMPERATURE CONTROL")
    print("  No More Pi Reboots! 🎉")
    print("="*60)
    
    init_db()
    load_thresholds()
    
    while True:
        show_main_menu()
        
        try:
            choice = input("\nSelect (1-3): ").strip()
            
            if choice == "1":
                run_console_control()
                input("\nPress Enter to return to menu...")
                
            elif choice == "2":
                run_web_server()
                input("\nPress Enter to return to menu...")
                
            elif choice == "3":
                print("\nExiting...")
                print("Goodbye! 👋\n")
                break
                
            else:
                print("\n⚠️  Invalid option! Choose 1, 2, or 3.")
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\n\nExiting...")
            print("Goodbye! 👋\n")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()