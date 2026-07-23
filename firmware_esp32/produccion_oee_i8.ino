#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// =======================================================
// PROYECTO I8 - CONTROL DE PRODUCCIÓN OEE
// ESP32 + HC-SR04 + LCD I2C + BOTONES + LEDS
// PAUSAS: 1 AVERIA / 2 LOGISTICA
// SIN SERVO
// =======================================================

// -------------------- PINES --------------------
const int PIN_TRIG = 5;
const int PIN_ECHO = 13;

const int BTN_VERDE = 14;   // Verde: ON/OFF o seleccionar AVERIA
const int BTN_PAUSA = 12;   // Amarillo: PAUSA / REANUDAR
const int BTN_RESET = 26;   // Rojo: RESET o seleccionar LOGISTICA

const int LED_VERDE = 15;
const int LED_ROJO = 27;

// -------------------- LCD --------------------
LiquidCrystal_I2C lcd(0x27, 16, 2);

// -------------------- VARIABLES --------------------
bool maquinaEncendida = false;
bool maquinaPausada = false;
bool esperandoMotivoParada = false;

String motivoParada = "NINGUNA";

unsigned long piezas = 0;
int metaPedido = 120;

float distanciaCm = 0.0;
bool piezaPresente = false;

// Calibración de tu maqueta
const float UMBRAL_DETECCION_CM = 8.5;
const float UMBRAL_LIBERACION_CM = 10.0;

const unsigned long ANTIRREBOTE_PIEZA_MS = 700;
unsigned long ultimoConteoMs = 0;

unsigned long inicioTurno = 0;
unsigned long inicioPausa = 0;
unsigned long tiempoPausaAcumulado = 0;

unsigned long ultimoTriggerMs = 0;
unsigned long ultimaPantallaMs = 0;
unsigned long ultimoSerialMs = 0;

unsigned long ultimoBotonVerdeMs = 0;
unsigned long ultimoBotonPausaMs = 0;
unsigned long ultimoBotonResetMs = 0;

const unsigned long DEBOUNCE_MS = 250;

// -------------------- SENSOR --------------------
volatile unsigned long echoInicio = 0;
volatile unsigned long echoDuracion = 0;
volatile bool nuevaMedicion = false;

// =======================================================
// INTERRUPCIÓN DEL HC-SR04
// =======================================================
void IRAM_ATTR interrupcionEcho() {
  if (digitalRead(PIN_ECHO) == HIGH) {
    echoInicio = micros();
  } else {
    echoDuracion = micros() - echoInicio;
    nuevaMedicion = true;
  }
}

// =======================================================
// ESTADO PARA PYTHON
// =======================================================
String obtenerEstadoMaquina() {
  if (!maquinaEncendida) {
    return "APAGADO";
  }

  if (maquinaPausada) {
    return "PAUSA";
  }

  return "OPERATIVO";
}

// =======================================================
// LCD - MENÚ DE PAUSA
// =======================================================
void mostrarMenuParada() {
  lcd.backlight();
  lcd.clear();

  lcd.setCursor(0, 0);
  lcd.print("1:AVERIA");

  lcd.setCursor(0, 1);
  lcd.print("2:LOGISTICA");
}

void seleccionarMotivoParada(String motivo) {
  if (!maquinaEncendida || !maquinaPausada) {
    return;
  }

  motivoParada = motivo;
  esperandoMotivoParada = false;

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("PAUSA:");

  lcd.setCursor(0, 1);
  lcd.print(motivoParada);

  Serial.print("PARADA_SELECCIONADA,motivo=");
  Serial.println(motivoParada);
}

// =======================================================
// RECIBIR COMANDOS DESDE PYTHON
// Acepta:
// META:20
// META=20
// PARADA:AVERIA
// PARADA:LOGISTICA
// =======================================================
void leerComandosPython() {
  while (Serial.available() > 0) {
    String comando = Serial.readStringUntil('\n');
    comando.trim();

    if (comando.length() == 0) {
      continue;
    }

    if (comando.startsWith("META:") || comando.startsWith("META=")) {
      int nuevaMeta = comando.substring(5).toInt();

      if (nuevaMeta > 0) {
        metaPedido = nuevaMeta;

        piezas = 0;
        piezaPresente = false;
        ultimoConteoMs = 0;

        inicioTurno = millis();
        tiempoPausaAcumulado = 0;
        motivoParada = "NINGUNA";
        esperandoMotivoParada = false;

        lcd.backlight();
        lcd.clear();

        lcd.setCursor(0, 0);
        lcd.print("Meta recibida");

        lcd.setCursor(0, 1);
        lcd.print("Meta:");
        lcd.print(metaPedido);

        Serial.print("META_OK,meta=");
        Serial.println(metaPedido);

        delay(1000);
      }
    }

    if (comando.startsWith("PARADA:") || comando.startsWith("PARADA=")) {
      String motivo = comando.substring(7);
      motivo.trim();
      motivo.toUpperCase();

      if (motivo.indexOf("AVER") >= 0) {
        seleccionarMotivoParada("AVERIA");
      }

      if (motivo.indexOf("LOG") >= 0) {
        seleccionarMotivoParada("LOGISTICA");
      }
    }
  }
}

// =======================================================
// SENSOR ULTRASÓNICO
// =======================================================
void lanzarPulsoUltrasonico() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);

  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);

  digitalWrite(PIN_TRIG, LOW);
}

void procesarSensor() {
  // Bloquea el conteo cuando está apagado o en pausa
  if (!maquinaEncendida || maquinaPausada) {
    return;
  }

  if (nuevaMedicion) {
    noInterrupts();
    unsigned long duracion = echoDuracion;
    nuevaMedicion = false;
    interrupts();

    distanciaCm = (duracion * 0.0343) / 2.0;

    // Filtro de lecturas falsas
    if (distanciaCm < 2.0 || distanciaCm > 80.0) {
      return;
    }

    // Detecta pieza cuando está cerca
    if (distanciaCm <= UMBRAL_DETECCION_CM && !piezaPresente) {
      if (millis() - ultimoConteoMs >= ANTIRREBOTE_PIEZA_MS) {
        piezas++;
        piezaPresente = true;
        ultimoConteoMs = millis();

        Serial.print("PIEZA DETECTADA | Total: ");
        Serial.println(piezas);
      }
    }

    // Libera el sensor cuando la pieza se aleja
    if (distanciaCm >= UMBRAL_LIBERACION_CM) {
      piezaPresente = false;
    }
  }
}

// =======================================================
// CÁLCULOS OEE
// =======================================================
unsigned long tiempoTotalMs() {
  if (!maquinaEncendida) {
    return 0;
  }

  return millis() - inicioTurno;
}

unsigned long tiempoProductivoMs() {
  if (!maquinaEncendida) {
    return 0;
  }

  unsigned long total = tiempoTotalMs();
  unsigned long pausa = tiempoPausaAcumulado;

  if (maquinaPausada) {
    pausa += millis() - inicioPausa;
  }

  if (pausa >= total) {
    return 0;
  }

  return total - pausa;
}

float calcularTasaHora() {
  float horas = (float)tiempoProductivoMs() / 3600000.0;

  if (horas <= 0.0) {
    return 0.0;
  }

  return (float)piezas / horas;
}

float calcularDisponibilidad() {
  unsigned long total = tiempoTotalMs();

  if (total == 0) {
    return 0.0;
  }

  return (float)tiempoProductivoMs() / (float)total;
}

float calcularRendimiento() {
  float tasa = calcularTasaHora();
  float rendimiento = tasa / 120.0;

  if (rendimiento > 1.0) {
    rendimiento = 1.0;
  }

  if (rendimiento < 0.0) {
    rendimiento = 0.0;
  }

  return rendimiento;
}

float calcularOEE() {
  return calcularDisponibilidad() * calcularRendimiento();
}

float calcularPorcentajeCumplimiento() {
  if (metaPedido <= 0) {
    return 0.0;
  }

  float porcentaje = ((float)piezas / (float)metaPedido) * 100.0;

  if (porcentaje > 100.0) {
    porcentaje = 100.0;
  }

  return porcentaje;
}

// =======================================================
// ENCENDER / APAGAR / PAUSA
// =======================================================
void prenderMaquina() {
  maquinaEncendida = true;
  maquinaPausada = false;
  esperandoMotivoParada = false;
  motivoParada = "NINGUNA";

  piezas = 0;
  distanciaCm = 0.0;
  piezaPresente = false;
  nuevaMedicion = false;

  inicioTurno = millis();
  inicioPausa = 0;
  tiempoPausaAcumulado = 0;
  ultimoConteoMs = 0;

  lcd.backlight();
  lcd.clear();

  lcd.setCursor(0, 0);
  lcd.print("Pz:0/");
  lcd.print(metaPedido);

  lcd.setCursor(0, 1);
  lcd.print("OPERATIVO");

  digitalWrite(LED_VERDE, HIGH);
  digitalWrite(LED_ROJO, LOW);

  Serial.println("MAQUINA ENCENDIDA");
}

void apagarMaquina() {
  maquinaEncendida = false;
  maquinaPausada = false;
  esperandoMotivoParada = false;
  motivoParada = "NINGUNA";

  piezas = 0;
  distanciaCm = 0.0;
  piezaPresente = false;
  nuevaMedicion = false;

  digitalWrite(LED_VERDE, LOW);
  digitalWrite(LED_ROJO, LOW);

  lcd.clear();
  lcd.noBacklight();

  Serial.println("MAQUINA APAGADA");
}

void entrarEnPausa() {
  maquinaPausada = true;
  esperandoMotivoParada = true;
  motivoParada = "SELECCIONAR";

  inicioPausa = millis();

  digitalWrite(LED_VERDE, LOW);
  digitalWrite(LED_ROJO, HIGH);

  mostrarMenuParada();

  Serial.println("MAQUINA EN PAUSA");
  Serial.println("MENU_PARADA,1=AVERIA,2=LOGISTICA");
}

void reanudarMaquina() {
  if (esperandoMotivoParada) {
    lcd.clear();

    lcd.setCursor(0, 0);
    lcd.print("Elija motivo:");

    lcd.setCursor(0, 1);
    lcd.print("1 Aver 2 Log");

    delay(800);
    mostrarMenuParada();
    return;
  }

  maquinaPausada = false;

  tiempoPausaAcumulado += millis() - inicioPausa;
  inicioPausa = 0;

  digitalWrite(LED_VERDE, HIGH);
  digitalWrite(LED_ROJO, LOW);

  lcd.clear();

  lcd.setCursor(0, 0);
  lcd.print("OPERATIVO");

  lcd.setCursor(0, 1);
  lcd.print("Pz:");
  lcd.print(piezas);
  lcd.print("/");
  lcd.print(metaPedido);

  Serial.println("MAQUINA REANUDADA");
}

// =======================================================
// BOTONES
// =======================================================
void leerBotones() {
  // Botón verde
  if (digitalRead(BTN_VERDE) == LOW && millis() - ultimoBotonVerdeMs > DEBOUNCE_MS) {
    ultimoBotonVerdeMs = millis();

    // Si está en el menú de pausa, verde selecciona AVERIA
    if (maquinaEncendida && maquinaPausada && esperandoMotivoParada) {
      seleccionarMotivoParada("AVERIA");
    } else {
      if (maquinaEncendida) {
        apagarMaquina();
      } else {
        prenderMaquina();
      }
    }
  }

  // Botón amarillo
  if (digitalRead(BTN_PAUSA) == LOW && millis() - ultimoBotonPausaMs > DEBOUNCE_MS) {
    ultimoBotonPausaMs = millis();

    if (maquinaEncendida) {
      if (!maquinaPausada) {
        entrarEnPausa();
      } else {
        reanudarMaquina();
      }
    }
  }

  // Botón rojo
  if (digitalRead(BTN_RESET) == LOW && millis() - ultimoBotonResetMs > DEBOUNCE_MS) {
    ultimoBotonResetMs = millis();

    // Si está en el menú de pausa, rojo selecciona LOGISTICA
    if (maquinaEncendida && maquinaPausada && esperandoMotivoParada) {
      seleccionarMotivoParada("LOGISTICA");
    } else {
      if (maquinaEncendida && !maquinaPausada) {
        piezas = 0;
        piezaPresente = false;

        inicioTurno = millis();
        tiempoPausaAcumulado = 0;
        motivoParada = "NINGUNA";

        lcd.clear();

        lcd.setCursor(0, 0);
        lcd.print("Conteo reset");

        lcd.setCursor(0, 1);
        lcd.print("Meta:");
        lcd.print(metaPedido);

        Serial.println("RESET DE CONTEO");
      }
    }
  }
}

// =======================================================
// SEMÁFORO
// =======================================================
void actualizarSemaforo() {
  if (!maquinaEncendida) {
    digitalWrite(LED_VERDE, LOW);
    digitalWrite(LED_ROJO, LOW);
    return;
  }

  if (maquinaPausada) {
    digitalWrite(LED_VERDE, LOW);
    digitalWrite(LED_ROJO, HIGH);
    return;
  }

  float tasa = calcularTasaHora();

  if (tasa >= 120.0 || piezas == 0) {
    digitalWrite(LED_VERDE, HIGH);
    digitalWrite(LED_ROJO, LOW);
  } else {
    digitalWrite(LED_VERDE, LOW);
    digitalWrite(LED_ROJO, HIGH);
  }
}

// =======================================================
// LCD
// =======================================================
void actualizarLCD() {
  if (!maquinaEncendida) {
    return;
  }

  if (maquinaPausada) {
    if (esperandoMotivoParada) {
      mostrarMenuParada();
    } else {
      lcd.clear();

      lcd.setCursor(0, 0);
      lcd.print("PAUSA:");

      lcd.setCursor(0, 1);
      lcd.print(motivoParada);
    }

    return;
  }

  lcd.clear();

  lcd.setCursor(0, 0);
  lcd.print("Pz:");
  lcd.print(piezas);
  lcd.print("/");
  lcd.print(metaPedido);

  lcd.setCursor(0, 1);
  lcd.print("RUN ");
  lcd.print(calcularPorcentajeCumplimiento(), 0);
  lcd.print("% ");
  lcd.print(calcularTasaHora(), 0);
  lcd.print("p/h");
}

// =======================================================
// SERIAL PARA PYTHON
// =======================================================
void enviarSerial() {
  Serial.print("DATA,");
  Serial.print("estado=");
  Serial.print(obtenerEstadoMaquina());
  Serial.print(",motivo=");
  Serial.print(motivoParada);
  Serial.print(",piezas=");
  Serial.print(piezas);
  Serial.print(",meta=");
  Serial.print(metaPedido);
  Serial.print(",distancia=");
  Serial.print(distanciaCm, 1);
  Serial.print(",tasa_h=");
  Serial.print(calcularTasaHora(), 1);
  Serial.print(",porcentaje=");
  Serial.print(calcularPorcentajeCumplimiento(), 1);
  Serial.print(",disponibilidad=");
  Serial.print(calcularDisponibilidad() * 100.0, 1);
  Serial.print(",rendimiento=");
  Serial.print(calcularRendimiento() * 100.0, 1);
  Serial.print(",OEE=");
  Serial.print(calcularOEE() * 100.0, 1);
  Serial.println("%");
}

// =======================================================
// SETUP
// =======================================================
void setup() {
  Serial.begin(115200);
  Serial.setTimeout(50);

  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);

  pinMode(BTN_VERDE, INPUT_PULLUP);
  pinMode(BTN_PAUSA, INPUT_PULLUP);
  pinMode(BTN_RESET, INPUT_PULLUP);

  pinMode(LED_VERDE, OUTPUT);
  pinMode(LED_ROJO, OUTPUT);

  digitalWrite(LED_VERDE, LOW);
  digitalWrite(LED_ROJO, LOW);

  Wire.begin(21, 22);

  lcd.init();
  lcd.clear();
  lcd.noBacklight();

  attachInterrupt(digitalPinToInterrupt(PIN_ECHO), interrupcionEcho, CHANGE);

  Serial.println("Sistema apagado.");
  Serial.println("Boton verde: ON/OFF o AVERIA.");
  Serial.println("Boton amarillo: PAUSA/REANUDAR.");
  Serial.println("Boton rojo: RESET o LOGISTICA.");
}

// =======================================================
// LOOP
// =======================================================
void loop() {
  leerComandosPython();

  leerBotones();

  if (maquinaEncendida) {
    if (millis() - ultimoTriggerMs >= 120) {
      ultimoTriggerMs = millis();
      lanzarPulsoUltrasonico();
    }

    procesarSensor();
    actualizarSemaforo();

    if (millis() - ultimaPantallaMs >= 700) {
      ultimaPantallaMs = millis();
      actualizarLCD();
    }
  }

  if (millis() - ultimoSerialMs >= 1000) {
    ultimoSerialMs = millis();
    enviarSerial();
  }
}
