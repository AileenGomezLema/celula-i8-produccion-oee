import tkinter as tk
from tkinter import ttk, messagebox
import serial
import time
import csv
import requests
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


# ==========================================
# CONFIGURACIÓN SERIAL
# ==========================================
PUERTO_SERIAL = "COM8"   # Cambia si tu ESP32 está en otro COM
BAUD_RATE = 115200

TASA_IDEAL_PIEZAS_HORA = 120

# ==========================================
# CONFIGURACIÓN THINGSPEAK / IoT
# ==========================================
THINGSPEAK_WRITE_API_KEY = "O388NHNM65IYQLBF"
THINGSPEAK_URL = "https://api.thingspeak.com/update.json"

# ThingSpeak no debe recibir datos cada segundo.
# Se envía cada 20 segundos.
INTERVALO_IOT_SEGUNDOS = 20
ultimo_envio_iot = 0

arduino = None


def conectar_serial():
    global arduino

    try:
        if arduino and arduino.is_open:
            arduino.close()

        arduino = serial.Serial(PUERTO_SERIAL, BAUD_RATE, timeout=0.1)
        time.sleep(2)

        print(f"Conectado a {PUERTO_SERIAL}")
        return True

    except Exception as e:
        print(f"Error al conectar con {PUERTO_SERIAL}: {e}")
        arduino = None
        return False


conectar_serial()


# ==========================================
# VARIABLES GLOBALES
# ==========================================
meta_actual = 120
historial_tiempo = []
historial_piezas = []

ultimo_tiempo_datos = 0
piezas_anteriores = -1


# ==========================================
# APLICACIÓN PRINCIPAL
# ==========================================
class AppOEE(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Sistema de Control de Producción OEE - HMI")
        self.geometry("400x240")
        self.resizable(False, False)

        self.frame_meta = tk.Frame(self)
        self.frame_dashboard = tk.Frame(self, bg="#F0F2F5")

        self.estado_actual = "APAGADO"
        self.motivo_parada_actual = "NINGUNO"

        self.piezas_actuales = 0
        self.tasa_actual = 0.0
        self.cumplimiento_actual = 0.0

        self.tiempo_inicio_turno = None
        self.tiempo_inicio_pausa = None

        self.tiempo_averia_acumulado = 0.0
        self.tiempo_logistica_acumulado = 0.0
        self.tiempo_sin_clasificar_acumulado = 0.0

        self.meta_cumplida = False
        self.reporte_guardado = False

        self.crear_pantalla_meta()
        self.frame_meta.pack(fill="both", expand=True)

    # ==========================================
    # UTILIDADES
    # ==========================================
    def formato_tiempo(self, segundos):
        segundos = int(segundos)

        if segundos < 0:
            segundos = 0

        h = segundos // 3600
        m = (segundos % 3600) // 60
        s = segundos % 60

        return f"{h:02d}:{m:02d}:{s:02d}"

    def enviar_comando_serial(self, comando):
        if arduino and arduino.is_open:
            try:
                arduino.write((comando + "\n").encode("utf-8"))
                arduino.flush()
                print("Enviado:", comando)
            except Exception as e:
                print("Error enviando comando:", e)

    # ==========================================
    # THINGSPEAK / IoT
    # ==========================================
    def codigo_estado(self):
        if self.estado_actual == "APAGADO":
            return 0
        elif self.estado_actual == "OPERATIVO":
            return 1
        elif self.estado_actual == "PAUSA":
            return 2
        elif self.estado_actual == "META CUMPLIDA":
            return 3
        else:
            return 0

    def codigo_motivo(self):
        if self.motivo_parada_actual == "AVERIA":
            return 1
        elif self.motivo_parada_actual == "LOGISTICA":
            return 2
        elif self.motivo_parada_actual == "SIN CLASIFICAR":
            return 3
        else:
            return 0

    def enviar_iot_thingspeak(self):
        global ultimo_envio_iot

        ahora = time.time()

        if ahora - ultimo_envio_iot < INTERVALO_IOT_SEGUNDOS:
            return

        ultimo_envio_iot = ahora

        (
            tiempo_total,
            tiempo_pausa,
            tiempo_productivo,
            tiempo_averia,
            tiempo_logistica,
            tiempo_sin_clasificar
        ) = self.obtener_tiempos()

        disponibilidad, rendimiento, calidad, oee = self.calcular_metricas_oee()

        datos_iot = {
            "api_key": THINGSPEAK_WRITE_API_KEY,
            "field1": self.piezas_actuales,
            "field2": meta_actual,
            "field3": round(self.tasa_actual, 2),
            "field4": round(disponibilidad, 2),
            "field5": round(oee, 2),
            "field6": self.codigo_estado(),
            "field7": self.codigo_motivo(),
            "field8": round(self.cumplimiento_actual, 2)
        }

        try:
            respuesta = requests.get(
                THINGSPEAK_URL,
                params=datos_iot,
                timeout=5
            )

            if respuesta.status_code == 200 and respuesta.text != "0":
                print("IoT enviado a ThingSpeak. Entry:", respuesta.text)
                self.lbl_status_bar.config(
                    text=f"Conectado: {PUERTO_SERIAL} | IoT enviado a ThingSpeak | Entry: {respuesta.text}"
                )
            else:
                print("Error ThingSpeak:", respuesta.status_code, respuesta.text)

        except Exception as e:
            print("Error enviando a ThingSpeak:", e)

    # ==========================================
    # PANTALLA 1: META
    # ==========================================
    def crear_pantalla_meta(self):
        tk.Label(
            self.frame_meta,
            text="Control de Producción OEE",
            font=("Arial", 16, "bold")
        ).pack(pady=18)

        tk.Label(
            self.frame_meta,
            text="Ingrese la meta de piezas del pedido:",
            font=("Arial", 11)
        ).pack(pady=5)

        self.entry_meta = tk.Entry(
            self.frame_meta,
            font=("Arial", 14),
            justify="center",
            width=15
        )
        self.entry_meta.pack(pady=8)
        self.entry_meta.insert(0, "120")

        tk.Button(
            self.frame_meta,
            text="Aceptar",
            font=("Arial", 11, "bold"),
            bg="#4CAF50",
            fg="white",
            padx=18,
            pady=6,
            command=self.enviar_meta_y_cambiar_pantalla
        ).pack(pady=15)

    def enviar_meta_y_cambiar_pantalla(self):
        global meta_actual, piezas_anteriores, ultimo_tiempo_datos, ultimo_envio_iot

        meta_texto = self.entry_meta.get().strip()

        if not meta_texto.isdigit():
            messagebox.showerror("Error", "Ingresa un número entero válido.")
            return

        meta_val = int(meta_texto)

        if meta_val <= 0:
            messagebox.showerror("Error", "La meta debe ser mayor a 0.")
            return

        meta_actual = meta_val

        self.estado_actual = "APAGADO"
        self.motivo_parada_actual = "NINGUNO"

        self.piezas_actuales = 0
        self.tasa_actual = 0.0
        self.cumplimiento_actual = 0.0

        self.tiempo_inicio_turno = None
        self.tiempo_inicio_pausa = None

        self.tiempo_averia_acumulado = 0.0
        self.tiempo_logistica_acumulado = 0.0
        self.tiempo_sin_clasificar_acumulado = 0.0

        self.meta_cumplida = False
        self.reporte_guardado = False

        piezas_anteriores = -1
        ultimo_tiempo_datos = 0
        ultimo_envio_iot = 0

        historial_tiempo.clear()
        historial_piezas.clear()

        if not (arduino and arduino.is_open):
            conectar_serial()

        if arduino and arduino.is_open:
            for i in range(3):
                self.enviar_comando_serial(f"META:{meta_val}")
                time.sleep(0.15)
                self.enviar_comando_serial(f"META={meta_val}")
                time.sleep(0.15)

        self.frame_meta.pack_forget()
        self.geometry("1120x680")
        self.resizable(False, False)

        self.crear_dashboard()
        self.frame_dashboard.pack(fill="both", expand=True)

        self.after(100, self.procesar_datos_serial)

    # ==========================================
    # DASHBOARD
    # ==========================================
    def crear_dashboard(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#F0F2F5")
        style.configure("Card.TFrame", background="white", relief="ridge", borderwidth=1)
        style.configure("TProgressbar", thickness=20)

        frame_izq = ttk.Frame(self.frame_dashboard, style="Card.TFrame", padding=20)
        frame_izq.place(x=20, y=20, width=300, height=600)

        frame_cen = ttk.Frame(self.frame_dashboard, style="Card.TFrame", padding=20)
        frame_cen.place(x=340, y=20, width=450, height=600)

        frame_der = ttk.Frame(self.frame_dashboard, style="Card.TFrame", padding=20)
        frame_der.place(x=810, y=20, width=290, height=600)

        self.lbl_status_bar = tk.Label(
            self.frame_dashboard,
            text=f"Conectado: {PUERTO_SERIAL} | Esperando datos...",
            bg="#F0F2F5",
            fg="gray",
            font=("Arial", 9)
        )
        self.lbl_status_bar.place(x=20, y=635)

        # ===============================
        # PANEL IZQUIERDO
        # ===============================
        tk.Label(
            frame_izq,
            text="Producción en Tiempo Real",
            font=("Arial", 12, "bold"),
            bg="white"
        ).pack(pady=(0, 15))

        tk.Label(
            frame_izq,
            text="Piezas Contadas",
            font=("Arial", 10),
            bg="white"
        ).pack()

        self.lbl_piezas = tk.Label(
            frame_izq,
            text="0",
            font=("Arial", 36, "bold"),
            fg="#1D7831",
            bg="white"
        )
        self.lbl_piezas.pack(pady=(0, 12))

        ttk.Separator(frame_izq, orient="horizontal").pack(fill="x", pady=5)

        tk.Label(
            frame_izq,
            text="Tasa Actual",
            font=("Arial", 10),
            bg="white"
        ).pack()

        self.lbl_tasa = tk.Label(
            frame_izq,
            text="0 p/h",
            font=("Arial", 28, "bold"),
            fg="#2B5BDE",
            bg="white"
        )
        self.lbl_tasa.pack(pady=(0, 12))

        ttk.Separator(frame_izq, orient="horizontal").pack(fill="x", pady=5)

        tk.Label(
            frame_izq,
            text="Cumplimiento del Pedido",
            font=("Arial", 10),
            bg="white"
        ).pack()

        self.lbl_cumplimiento = tk.Label(
            frame_izq,
            text="0 %",
            font=("Arial", 28, "bold"),
            fg="#E67E22",
            bg="white"
        )
        self.lbl_cumplimiento.pack(pady=(5, 5))

        self.progreso_var = tk.DoubleVar()

        self.barra_progreso = ttk.Progressbar(
            frame_izq,
            variable=self.progreso_var,
            maximum=100,
            style="TProgressbar"
        )
        self.barra_progreso.pack(fill="x", pady=5)

        ttk.Separator(frame_izq, orient="horizontal").pack(fill="x", pady=15)

        tk.Label(
            frame_izq,
            text="OEE Actual",
            font=("Arial", 10),
            bg="white"
        ).pack()

        self.lbl_oee_actual = tk.Label(
            frame_izq,
            text="0 %",
            font=("Arial", 30, "bold"),
            fg="#7C3AED",
            bg="white"
        )
        self.lbl_oee_actual.pack(pady=(5, 5))

        # ===============================
        # PANEL CENTRAL
        # ===============================
        tk.Label(
            frame_cen,
            text="Historial de Producción",
            font=("Arial", 12, "bold"),
            bg="white"
        ).pack(pady=(0, 10))

        self.fig, self.ax = plt.subplots(figsize=(5.2, 3.2), dpi=100)
        self.fig.patch.set_facecolor("white")
        self.ax.set_facecolor("#FAFAFA")
        self.ax.set_ylabel("Piezas acumuladas")
        self.ax.set_xlabel("Hora")
        self.ax.grid(True, linestyle="--", alpha=0.6)

        self.canvas = FigureCanvasTkAgg(self.fig, master=frame_cen)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        ttk.Separator(frame_cen, orient="horizontal").pack(fill="x", pady=15)

        tk.Label(
            frame_cen,
            text="Métricas del Turno",
            font=("Arial", 12, "bold"),
            bg="white"
        ).pack(pady=(0, 8))

        self.lbl_tiempo_turno = tk.Label(
            frame_cen,
            text="Tiempo de turno: 00:00:00",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_tiempo_turno.pack(anchor="w", pady=2)

        self.lbl_tiempo_pausa = tk.Label(
            frame_cen,
            text="Tiempo total en pausa: 00:00:00",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_tiempo_pausa.pack(anchor="w", pady=2)

        self.lbl_tiempo_productivo = tk.Label(
            frame_cen,
            text="Tiempo productivo: 00:00:00",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_tiempo_productivo.pack(anchor="w", pady=2)

        ttk.Separator(frame_cen, orient="horizontal").pack(fill="x", pady=10)

        self.lbl_tiempo_averia = tk.Label(
            frame_cen,
            text="Tiempo por avería: 00:00:00",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_tiempo_averia.pack(anchor="w", pady=2)

        self.lbl_tiempo_logistica = tk.Label(
            frame_cen,
            text="Tiempo por logística: 00:00:00",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_tiempo_logistica.pack(anchor="w", pady=2)

        self.lbl_tiempo_sin_clasificar = tk.Label(
            frame_cen,
            text="Pausa sin clasificar: 00:00:00",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_tiempo_sin_clasificar.pack(anchor="w", pady=2)

        ttk.Separator(frame_cen, orient="horizontal").pack(fill="x", pady=10)

        self.lbl_disponibilidad = tk.Label(
            frame_cen,
            text="Disponibilidad: 0 %",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_disponibilidad.pack(anchor="w", pady=2)

        self.lbl_caida_disponibilidad = tk.Label(
            frame_cen,
            text="Caída instantánea de disponibilidad: 0 %",
            font=("Arial", 11, "bold"),
            bg="white",
            fg="#D32F2F"
        )
        self.lbl_caida_disponibilidad.pack(anchor="w", pady=2)

        self.lbl_rendimiento = tk.Label(
            frame_cen,
            text="Rendimiento: 0 %",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_rendimiento.pack(anchor="w", pady=2)

        self.lbl_calidad = tk.Label(
            frame_cen,
            text="Calidad: 100 %",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_calidad.pack(anchor="w", pady=2)

        # ===============================
        # PANEL DERECHO
        # ===============================
        tk.Label(
            frame_der,
            text="Estado del Sistema",
            font=("Arial", 11, "bold"),
            bg="white"
        ).pack(pady=(0, 5))

        self.lbl_estado = tk.Label(
            frame_der,
            text="APAGADO",
            font=("Arial", 18, "bold"),
            fg="#D32F2F",
            bg="white"
        )
        self.lbl_estado.pack(pady=10)

        self.lbl_motivo = tk.Label(
            frame_der,
            text="Motivo de parada: NINGUNO",
            font=("Arial", 13, "bold"),
            bg="white",
            fg="#374151",
            wraplength=240,
            justify="center"
        )
        self.lbl_motivo.pack(pady=10)

        ttk.Separator(frame_der, orient="horizontal").pack(fill="x", pady=12)

        tk.Label(
            frame_der,
            text="Pedido Activo",
            font=("Arial", 11, "bold"),
            bg="white"
        ).pack(pady=(0, 5))

        tk.Label(
            frame_der,
            text="Lote: CA-2025-05",
            font=("Arial", 11),
            bg="white"
        ).pack(pady=3)

        self.lbl_objetivo = tk.Label(
            frame_der,
            text=f"Objetivo: {meta_actual} piezas",
            font=("Arial", 11),
            bg="white"
        )
        self.lbl_objetivo.pack(pady=3)

        ttk.Separator(frame_der, orient="horizontal").pack(fill="x", pady=12)

        tk.Label(
            frame_der,
            text="Tiempo Restante Estimado",
            font=("Arial", 11, "bold"),
            bg="white"
        ).pack(pady=(0, 8))

        self.lbl_tiempo_restante = tk.Label(
            frame_der,
            text="--:--:--",
            font=("Arial", 24, "bold"),
            bg="white",
            fg="#111827"
        )
        self.lbl_tiempo_restante.pack(pady=5)

        ttk.Separator(frame_der, orient="horizontal").pack(fill="x", pady=12)

        tk.Label(
            frame_der,
            text="OEE Final del Turno",
            font=("Arial", 11, "bold"),
            bg="white"
        ).pack(pady=(0, 5))

        self.lbl_oee_final = tk.Label(
            frame_der,
            text="0 %",
            font=("Arial", 26, "bold"),
            fg="#7C3AED",
            bg="white"
        )
        self.lbl_oee_final.pack(pady=5)

        self.lbl_alerta_meta = tk.Label(
            frame_der,
            text="Pedido en proceso",
            font=("Arial", 13, "bold"),
            bg="white",
            fg="#374151",
            wraplength=250,
            justify="center"
        )
        self.lbl_alerta_meta.pack(pady=10)

    # ==========================================
    # ESTADOS Y TIEMPOS
    # ==========================================
    def iniciar_pausa(self):
        self.tiempo_inicio_pausa = time.time()

        if self.motivo_parada_actual == "NINGUNO":
            self.motivo_parada_actual = "SIN CLASIFICAR"

    def cerrar_pausa(self):
        if self.tiempo_inicio_pausa is None:
            return

        duracion = time.time() - self.tiempo_inicio_pausa

        if self.motivo_parada_actual == "AVERIA":
            self.tiempo_averia_acumulado += duracion

        elif self.motivo_parada_actual == "LOGISTICA":
            self.tiempo_logistica_acumulado += duracion

        else:
            self.tiempo_sin_clasificar_acumulado += duracion

        self.tiempo_inicio_pausa = None
        self.motivo_parada_actual = "NINGUNO"

    def cambiar_estado(self, nuevo_estado):
        estado_anterior = self.estado_actual

        if estado_anterior != "PAUSA" and nuevo_estado == "PAUSA":
            self.iniciar_pausa()

        if estado_anterior == "PAUSA" and nuevo_estado != "PAUSA":
            self.cerrar_pausa()

        self.estado_actual = nuevo_estado

        if nuevo_estado == "OPERATIVO" and self.tiempo_inicio_turno is None:
            self.tiempo_inicio_turno = time.time()

    def obtener_tiempos(self):
        if self.tiempo_inicio_turno is None:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

        ahora = time.time()

        tiempo_total = ahora - self.tiempo_inicio_turno

        tiempo_averia = self.tiempo_averia_acumulado
        tiempo_logistica = self.tiempo_logistica_acumulado
        tiempo_sin_clasificar = self.tiempo_sin_clasificar_acumulado

        if self.estado_actual == "PAUSA" and self.tiempo_inicio_pausa is not None:
            pausa_actual = ahora - self.tiempo_inicio_pausa

            if self.motivo_parada_actual == "AVERIA":
                tiempo_averia += pausa_actual

            elif self.motivo_parada_actual == "LOGISTICA":
                tiempo_logistica += pausa_actual

            else:
                tiempo_sin_clasificar += pausa_actual

        tiempo_pausa_total = tiempo_averia + tiempo_logistica + tiempo_sin_clasificar
        tiempo_productivo = tiempo_total - tiempo_pausa_total

        if tiempo_productivo < 0:
            tiempo_productivo = 0

        return (
            tiempo_total,
            tiempo_pausa_total,
            tiempo_productivo,
            tiempo_averia,
            tiempo_logistica,
            tiempo_sin_clasificar
        )

    def calcular_metricas_oee(self):
        (
            tiempo_total,
            tiempo_pausa,
            tiempo_productivo,
            tiempo_averia,
            tiempo_logistica,
            tiempo_sin_clasificar
        ) = self.obtener_tiempos()

        if tiempo_total > 0:
            disponibilidad = (tiempo_productivo / tiempo_total) * 100
        else:
            disponibilidad = 0

        if TASA_IDEAL_PIEZAS_HORA > 0:
            rendimiento = (self.tasa_actual / TASA_IDEAL_PIEZAS_HORA) * 100
        else:
            rendimiento = 0

        if rendimiento > 100:
            rendimiento = 100

        if rendimiento < 0:
            rendimiento = 0

        calidad = 100

        oee = (disponibilidad / 100) * (rendimiento / 100) * (calidad / 100) * 100

        return disponibilidad, rendimiento, calidad, oee

    # ==========================================
    # LECTURA SERIAL
    # ==========================================
    def procesar_datos_serial(self):
        global ultimo_tiempo_datos, piezas_anteriores

        ahora_time = time.time()

        if arduino and arduino.is_open and arduino.in_waiting > 0:
            try:
                while arduino.in_waiting > 0:
                    linea = arduino.readline().decode("utf-8", errors="ignore").strip()

                    if not linea:
                        continue

                    print("SERIAL:", linea)

                    if "MAQUINA ENCENDIDA" in linea or "MAQUINA REANUDADA" in linea:
                        self.cambiar_estado("OPERATIVO")

                    elif "MAQUINA EN PAUSA" in linea:
                        self.cambiar_estado("PAUSA")

                    elif "MAQUINA APAGADA" in linea:
                        self.cambiar_estado("APAGADO")

                    elif "RESET DE CONTEO" in linea:
                        self.piezas_actuales = 0
                        piezas_anteriores = 0
                        historial_tiempo.clear()
                        historial_piezas.clear()
                        self.meta_cumplida = False
                        self.reporte_guardado = False
                        self.lbl_oee_final.config(text="0 %")

                    elif linea.startswith("PARADA_SELECCIONADA"):
                        partes = linea.split(",")

                        for p in partes:
                            if "motivo=" in p:
                                self.motivo_parada_actual = p.split("=", 1)[1].strip().upper()

                    elif linea.startswith("DATA"):
                        ultimo_tiempo_datos = ahora_time

                        partes = linea.split(",")
                        datos = {}

                        for p in partes[1:]:
                            if "=" in p:
                                k, v = p.split("=", 1)
                                datos[k.strip()] = v.strip().replace("%", "")

                        if "estado" in datos:
                            estado_serial = datos["estado"].strip().upper()

                            if estado_serial in ["OPERATIVO", "PAUSA", "APAGADO"]:
                                self.cambiar_estado(estado_serial)

                        if "motivo" in datos:
                            motivo_serial = datos["motivo"].strip().upper()

                            if motivo_serial == "AVERIA":
                                self.motivo_parada_actual = "AVERIA"

                            elif motivo_serial == "LOGISTICA":
                                self.motivo_parada_actual = "LOGISTICA"

                            elif motivo_serial == "SELECCIONAR":
                                self.motivo_parada_actual = "SIN CLASIFICAR"

                        self.piezas_actuales = int(float(datos.get("piezas", self.piezas_actuales)))
                        self.tasa_actual = float(datos.get("tasa_h", self.tasa_actual))

                        if meta_actual > 0:
                            self.cumplimiento_actual = (self.piezas_actuales / meta_actual) * 100
                        else:
                            self.cumplimiento_actual = 0

                        if self.cumplimiento_actual > 100:
                            self.cumplimiento_actual = 100

                        if self.piezas_actuales != piezas_anteriores:
                            piezas_anteriores = self.piezas_actuales

                            if self.estado_actual != "PAUSA":
                                self.cambiar_estado("OPERATIVO")

                        ahora_str = datetime.now().strftime("%H:%M:%S")
                        historial_tiempo.append(ahora_str)
                        historial_piezas.append(self.piezas_actuales)

                        if len(historial_tiempo) > 15:
                            historial_tiempo.pop(0)
                            historial_piezas.pop(0)

                        self.actualizar_grafica()

                        if (
                            self.piezas_actuales >= meta_actual
                            and meta_actual > 0
                            and not self.meta_cumplida
                        ):
                            self.meta_cumplida = True
                            self.cambiar_estado("META CUMPLIDA")
                            self.mostrar_meta_cumplida()

            except Exception as e:
                print(f"Error parseando serial: {e}")

        if ahora_time - ultimo_tiempo_datos > 4.0 and ultimo_tiempo_datos > 0:
            if self.estado_actual != "META CUMPLIDA":
                self.cambiar_estado("APAGADO")

        self.actualizar_interfaz()
        self.after(100, self.procesar_datos_serial)

    # ==========================================
    # GRÁFICA
    # ==========================================
    def actualizar_grafica(self):
        self.ax.clear()
        self.ax.set_facecolor("#FAFAFA")

        self.ax.plot(
            historial_tiempo,
            historial_piezas,
            marker="o",
            color="#2B5BDE",
            linestyle="-",
            linewidth=2,
            markersize=5
        )

        self.ax.set_ylabel("Piezas acumuladas")
        self.ax.set_xlabel("Hora")
        self.ax.grid(True, linestyle="--", alpha=0.6)
        self.fig.autofmt_xdate()
        self.canvas.draw()

    # ==========================================
    # ACTUALIZAR INTERFAZ
    # ==========================================
    def actualizar_interfaz(self):
        (
            tiempo_total,
            tiempo_pausa,
            tiempo_productivo,
            tiempo_averia,
            tiempo_logistica,
            tiempo_sin_clasificar
        ) = self.obtener_tiempos()

        disponibilidad, rendimiento, calidad, oee = self.calcular_metricas_oee()

        if tiempo_total > 0:
            caida_disponibilidad = 100 - disponibilidad
        else:
            caida_disponibilidad = 0

        if caida_disponibilidad < 0:
            caida_disponibilidad = 0

        if self.piezas_actuales >= meta_actual and meta_actual > 0:
            txt_tiempo_restante = "00:00:00"

        elif self.tasa_actual > 0 and self.estado_actual == "OPERATIVO":
            piezas_faltantes = meta_actual - self.piezas_actuales

            if piezas_faltantes < 0:
                piezas_faltantes = 0

            segundos_restantes = (piezas_faltantes / self.tasa_actual) * 3600
            txt_tiempo_restante = self.formato_tiempo(segundos_restantes)

        else:
            txt_tiempo_restante = "--:--:--"

        self.lbl_piezas.config(text=f"{self.piezas_actuales}")
        self.lbl_tasa.config(text=f"{int(self.tasa_actual)} p/h")
        self.lbl_cumplimiento.config(text=f"{int(self.cumplimiento_actual)} %")
        self.progreso_var.set(self.cumplimiento_actual)

        self.lbl_oee_actual.config(text=f"{oee:.1f} %")

        self.lbl_tiempo_turno.config(
            text=f"Tiempo de turno: {self.formato_tiempo(tiempo_total)}"
        )

        self.lbl_tiempo_pausa.config(
            text=f"Tiempo total en pausa: {self.formato_tiempo(tiempo_pausa)}"
        )

        self.lbl_tiempo_productivo.config(
            text=f"Tiempo productivo: {self.formato_tiempo(tiempo_productivo)}"
        )

        self.lbl_tiempo_averia.config(
            text=f"Tiempo por avería: {self.formato_tiempo(tiempo_averia)}"
        )

        self.lbl_tiempo_logistica.config(
            text=f"Tiempo por logística: {self.formato_tiempo(tiempo_logistica)}"
        )

        self.lbl_tiempo_sin_clasificar.config(
            text=f"Pausa sin clasificar: {self.formato_tiempo(tiempo_sin_clasificar)}"
        )

        self.lbl_disponibilidad.config(
            text=f"Disponibilidad: {disponibilidad:.1f} %"
        )

        if self.estado_actual == "PAUSA" and self.motivo_parada_actual == "LOGISTICA":
            self.lbl_caida_disponibilidad.config(
                text=f"Caída instantánea de disponibilidad por logística: -{caida_disponibilidad:.1f} %",
                fg="#E67E22"
            )

        elif self.estado_actual == "PAUSA" and self.motivo_parada_actual == "AVERIA":
            self.lbl_caida_disponibilidad.config(
                text=f"Caída instantánea de disponibilidad por avería: -{caida_disponibilidad:.1f} %",
                fg="#D32F2F"
            )

        elif self.estado_actual == "PAUSA":
            self.lbl_caida_disponibilidad.config(
                text=f"Caída instantánea de disponibilidad: -{caida_disponibilidad:.1f} %",
                fg="#D32F2F"
            )

        else:
            self.lbl_caida_disponibilidad.config(
                text=f"Caída instantánea de disponibilidad: -{caida_disponibilidad:.1f} %",
                fg="#374151"
            )

        self.lbl_rendimiento.config(
            text=f"Rendimiento: {rendimiento:.1f} %"
        )

        self.lbl_calidad.config(
            text=f"Calidad: {calidad:.1f} %"
        )

        self.lbl_objetivo.config(text=f"Objetivo: {meta_actual} piezas")
        self.lbl_tiempo_restante.config(text=txt_tiempo_restante)

        if self.estado_actual == "OPERATIVO":
            self.lbl_estado.config(text="OPERATIVO", fg="#1D7831")
            self.lbl_alerta_meta.config(text="Pedido en proceso", fg="#374151")

        elif self.estado_actual == "PAUSA":
            self.lbl_estado.config(text="PAUSA", fg="#E67E22")
            self.lbl_alerta_meta.config(text="Sistema en pausa", fg="#E67E22")

        elif self.estado_actual == "APAGADO":
            self.lbl_estado.config(text="APAGADO", fg="#D32F2F")
            self.lbl_alerta_meta.config(text="Sistema apagado", fg="#D32F2F")

        elif self.estado_actual == "META CUMPLIDA":
            self.lbl_estado.config(text="META CUMPLIDA", fg="#2B5BDE")
            self.lbl_alerta_meta.config(text="Pedido terminado correctamente", fg="#2B5BDE")

        if self.estado_actual == "PAUSA":
            if self.motivo_parada_actual == "AVERIA":
                self.lbl_motivo.config(text="Motivo de parada: AVERÍA", fg="#D32F2F")

            elif self.motivo_parada_actual == "LOGISTICA":
                self.lbl_motivo.config(text="Motivo de parada: LOGÍSTICA", fg="#E67E22")

            else:
                self.lbl_motivo.config(text="Motivo de parada: SIN CLASIFICAR", fg="#E67E22")
        else:
            self.lbl_motivo.config(text="Motivo de parada: NINGUNO", fg="#374151")

        ahora_str = datetime.now().strftime("%H:%M:%S")

        self.lbl_status_bar.config(
            text=f"Conectado: {PUERTO_SERIAL} | Última actualización: {ahora_str}"
        )

        self.enviar_iot_thingspeak()

    # ==========================================
    # META CUMPLIDA Y REPORTE
    # ==========================================
    def mostrar_meta_cumplida(self):
        (
            tiempo_total,
            tiempo_pausa,
            tiempo_productivo,
            tiempo_averia,
            tiempo_logistica,
            tiempo_sin_clasificar
        ) = self.obtener_tiempos()

        disponibilidad, rendimiento, calidad, oee_final = self.calcular_metricas_oee()

        self.lbl_oee_final.config(text=f"{oee_final:.1f} %")

        archivo = self.guardar_reporte_final(
            tiempo_total,
            tiempo_pausa,
            tiempo_productivo,
            tiempo_averia,
            tiempo_logistica,
            tiempo_sin_clasificar,
            disponibilidad,
            rendimiento,
            calidad,
            oee_final
        )

        messagebox.showinfo(
            "Meta cumplida",
            f"Meta alcanzada correctamente.\n\n"
            f"Piezas producidas: {self.piezas_actuales}\n"
            f"Meta del pedido: {meta_actual}\n\n"
            f"Tiempo total: {self.formato_tiempo(tiempo_total)}\n"
            f"Tiempo total en pausa: {self.formato_tiempo(tiempo_pausa)}\n"
            f"Avería: {self.formato_tiempo(tiempo_averia)}\n"
            f"Logística: {self.formato_tiempo(tiempo_logistica)}\n"
            f"Sin clasificar: {self.formato_tiempo(tiempo_sin_clasificar)}\n"
            f"Tiempo productivo: {self.formato_tiempo(tiempo_productivo)}\n\n"
            f"Disponibilidad: {disponibilidad:.1f} %\n"
            f"Rendimiento: {rendimiento:.1f} %\n"
            f"Calidad: {calidad:.1f} %\n"
            f"OEE final: {oee_final:.1f} %\n\n"
            f"Reporte CSV: {archivo}"
        )

    def guardar_reporte_final(
        self,
        tiempo_total,
        tiempo_pausa,
        tiempo_productivo,
        tiempo_averia,
        tiempo_logistica,
        tiempo_sin_clasificar,
        disponibilidad,
        rendimiento,
        calidad,
        oee_final
    ):
        if self.reporte_guardado:
            return "ya_guardado"

        self.reporte_guardado = True

        fecha_archivo = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_archivo = f"reporte_oee_{fecha_archivo}.csv"

        try:
            with open(nombre_archivo, mode="w", newline="", encoding="utf-8") as archivo:
                writer = csv.writer(archivo)

                writer.writerow(["REPORTE FINAL OEE"])
                writer.writerow([])

                writer.writerow(["Fecha", datetime.now().strftime("%Y-%m-%d")])
                writer.writerow(["Hora", datetime.now().strftime("%H:%M:%S")])
                writer.writerow(["Lote", "CA-2025-05"])
                writer.writerow(["Meta de piezas", meta_actual])
                writer.writerow(["Piezas producidas", self.piezas_actuales])
                writer.writerow(["Tasa actual piezas/hora", round(self.tasa_actual, 2)])
                writer.writerow(["Cumplimiento %", round(self.cumplimiento_actual, 2)])
                writer.writerow([])

                writer.writerow(["Tiempo total segundos", round(tiempo_total, 2)])
                writer.writerow(["Tiempo total pausa segundos", round(tiempo_pausa, 2)])
                writer.writerow(["Tiempo averia segundos", round(tiempo_averia, 2)])
                writer.writerow(["Tiempo logistica segundos", round(tiempo_logistica, 2)])
                writer.writerow(["Tiempo sin clasificar segundos", round(tiempo_sin_clasificar, 2)])
                writer.writerow(["Tiempo productivo segundos", round(tiempo_productivo, 2)])
                writer.writerow([])

                writer.writerow(["Disponibilidad %", round(disponibilidad, 2)])
                writer.writerow(["Caida instantanea disponibilidad %", round(100 - disponibilidad, 2)])
                writer.writerow(["Rendimiento %", round(rendimiento, 2)])
                writer.writerow(["Calidad %", round(calidad, 2)])
                writer.writerow(["OEE final %", round(oee_final, 2)])
                writer.writerow([])

                writer.writerow(["Historial de producción"])
                writer.writerow(["Hora", "Piezas acumuladas"])

                for h, p in zip(historial_tiempo, historial_piezas):
                    writer.writerow([h, p])

            print("Reporte guardado:", nombre_archivo)
            return nombre_archivo

        except Exception as e:
            print("Error guardando reporte:", e)
            return "error"


# ==========================================
# EJECUCIÓN
# ==========================================
if __name__ == "__main__":
    app = AppOEE()

    def al_cerrar():
        if arduino and arduino.is_open:
            arduino.close()
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", al_cerrar)
    app.mainloop()
