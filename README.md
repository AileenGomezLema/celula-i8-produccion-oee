# Célula I8 - Producción OEE

Sistema IoT para monitoreo de producción artesanal mediante ESP32, sensor ultrasónico, LCD I2C, botones físicos, semáforo LED, HMI en Python y visualización histórica en ThingSpeak.

## Descripción del sistema

El sistema permite contar piezas de forma automática mediante un sensor ultrasónico. El usuario define una meta de producción desde una interfaz HMI desarrollada en Python. La meta se envía al ESP32 y se visualiza en una pantalla LCD física.

Durante la operación se registran piezas producidas, tasa de producción, cumplimiento del pedido, disponibilidad, rendimiento, calidad y OEE. Además, se clasifican paradas como avería o logística, afectando directamente el cálculo de disponibilidad.

## Componentes utilizados

- ESP32
- Sensor ultrasónico HC-SR04
- LCD 16x2 I2C
- Botones físicos de inicio, pausa y reset
- LEDs de estado
- HMI en Python
- ThingSpeak para monitoreo IoT
- Exportación de reporte CSV

## Funcionamiento general

1. El usuario ingresa la meta de producción desde el HMI en Python.
2. La meta se envía al ESP32 y aparece en la LCD.
3. El sistema cuenta piezas mediante el sensor ultrasónico.
4. El botón verde permite encender o apagar el sistema.
5. El botón amarillo permite pausar o reanudar la operación.
6. En pausa, la LCD muestra el menú de motivo de parada.
7. El botón verde selecciona avería.
8. El botón rojo selecciona logística.
9. Mientras el sistema está en pausa, el conteo de piezas se bloquea.
10. Python calcula la disponibilidad, rendimiento, calidad y OEE.
11. Los datos se envían a ThingSpeak para visualización histórica.
12. Al cumplir la meta, se genera un reporte CSV del turno.

## Campos enviados a ThingSpeak

| Campo | Variable |
|---|---|
| Field 1 | Piezas |
| Field 2 | Meta |
| Field 3 | Tasa piezas/h |
| Field 4 | Disponibilidad |
| Field 5 | OEE |
| Field 6 | Estado |
| Field 7 | Motivo de parada |
| Field 8 | Cumplimiento |

## Códigos de estado

| Código | Estado |
|---|---|
| 0 | Apagado |
| 1 | Operativo |
| 2 | Pausa |
| 3 | Meta cumplida |

## Códigos de motivo de parada

| Código | Motivo |
|---|---|
| 0 | Ninguno |
| 1 | Avería |
| 2 | Logística |
| 3 | Sin clasificar |

## Estructura del repositorio

- `firmware_esp32/`: firmware del ESP32.
- `hmi_python/`: script HMI en Python.
- `evidencias/`: capturas del sistema, LCD, HMI y ThingSpeak.
- `planos_vectoriales/`: archivos de corte láser o planos de la rampa.

## Institución

FICA - UCE 2026
