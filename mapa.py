import tkinter as tk
from tkinter import ttk, messagebox
import firebase_admin
from firebase_admin import credentials, firestore
from geopy.geocoders import Nominatim
import folium
from folium.features import DivIcon
import webbrowser
import os
import sys
from fpdf import FPDF

# --- FUNCIONES DE UTILIDAD ---
def obtener_ruta_real(archivo):
    """ Obtiene la ruta del archivo tanto en script como en ejecutable .exe """
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), archivo)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), archivo)

class SistemaMapaHMO:
    def __init__(self, root):
        self.root = root
        self.root.title("SISTEMA DE TRAYECTORIA ACADÉMICA PRO")
        self.root.geometry("650x880")
        self.root.configure(bg="#f1f5f9")

        self.geolocator = Nominatim(user_agent="mapa_escolar_hmo_v4")
        
        # Configuración de Colores y Categorías
        self.config_niveles = {
            "PREESCOLAR": {"color": "green", "rgb": (0, 128, 0)},
            "PRIMARIA": {"color": "blue", "rgb": (0, 0, 255)},
            "SECUNDARIA": {"color": "orange", "rgb": (255, 165, 0)},
            "PREPARATORIA": {"color": "red", "rgb": (255, 0, 0)},
            "UNIVERSIDAD": {"color": "purple", "rgb": (128, 0, 128)},
            "FUNDACIÓN O ORG": {"color": "cadetblue", "rgb": (95, 158, 160)}
        }

        # --- CONEXIÓN FIREBASE ---
        self.coleccion = None
        try:
            nombre_json = "dif-hermosillo-firebase-adminsdk-fbsvc-b793685b2c.json"
            cert_path = obtener_ruta_real(nombre_json)
            
            if not os.path.exists(cert_path):
                messagebox.showwarning("Archivo Faltante", f"No se encontró el archivo JSON:\n{nombre_json}")
            else:
                if not firebase_admin._apps:
                    cred = credentials.Certificate(cert_path)
                    firebase_admin.initialize_app(cred)
                self.db = firestore.client()
                self.coleccion = self.db.collection('trayectoria_escuelas')
        except Exception as e:
            messagebox.showerror("Error Firebase", f"Ocurrió un error al conectar: {e}")

        # --- INTERFAZ ---
        header = tk.Frame(root, bg="#1e293b", height=60)
        header.pack(fill="x")
        tk.Label(header, text="📍 GESTOR ACADÉMICO Y SOCIAL", font=("Arial", 12, "bold"), bg="#1e293b", fg="white").pack(pady=15)

        form = tk.Frame(root, bg="white", padx=15, pady=15)
        form.pack(pady=10, padx=20, fill="x")

        tk.Label(form, text="Nombre de la Institución / Organización:", bg="white", font=("Arial", 9, "bold")).pack(anchor="w")
        self.ent_nombre = tk.Entry(form, font=("Arial", 11)); self.ent_nombre.pack(fill="x", pady=5)

        tk.Label(form, text="Nivel / Tipo:", bg="white", font=("Arial", 9, "bold")).pack(anchor="w")
        self.cmb_nivel = ttk.Combobox(form, values=list(self.config_niveles.keys()), state="readonly")
        self.cmb_nivel.pack(fill="x", pady=5); self.cmb_nivel.set("PRIMARIA")

        # --- BOTONERA PRINCIPAL ---
        btn_f = tk.Frame(root, bg="#f1f5f9")
        btn_f.pack(fill="x", padx=20)
        btn_params = {"fg": "white", "font": ("Arial", 8, "bold"), "width": 12, "pady": 5}
        
        tk.Button(btn_f, text="💾 GUARDAR", bg="#10b981", command=self.guardar_datos, **btn_params).pack(side="left", expand=True, fill="x", padx=2)
        tk.Button(btn_f, text="➕ MANUAL", bg="#6366f1", command=self.ventana_manual, **btn_params).pack(side="left", expand=True, fill="x", padx=2)
        tk.Button(btn_f, text="🗺️ VER MAPA", bg="#3b82f6", command=self.generar_mapa, **btn_params).pack(side="left", expand=True, fill="x", padx=2)
        tk.Button(btn_f, text="📄 PDF", bg="#ef4444", command=self.generar_pdf, **btn_params).pack(side="left", expand=True, fill="x", padx=2)

        # --- SECCIÓN BOTÓN ESPECIAL ---
        cat_f = tk.Frame(root, bg="#f1f5f9")
        cat_f.pack(fill="x", padx=20, pady=10)
        
        def accion_fundacion():
            self.cmb_nivel.set("FUNDACIÓN O ORG")
            # Abre Google Maps con búsqueda en Hermosillo
            webbrowser.open("https://www.google.com/maps/search/fundaciones+en+Hermosillo")

        tk.Button(cat_f, text="🤝 FUNDACIÓN O ORGANIZACIÓN (BUSCAR EN MAPS)", bg="#475569", fg="white", 
                  font=("Arial", 9, "bold"), command=accion_fundacion, pady=8).pack(fill="x")

        # TABLA
        self.tabla = ttk.Treeview(root, columns=("Nombre", "Nivel"), show="headings", height=10)
        self.tabla.heading("Nombre", text="NOMBRE"); self.tabla.heading("Nivel", text="CATEGORÍA")
        self.tabla.pack(padx=20, pady=10, fill="both", expand=True)
        
        self.cargar_tabla()

    def ventana_manual(self):
        v_manual = tk.Toplevel(self.root)
        v_manual.title("Entrada Manual")
        v_manual.geometry("350x450"); v_manual.grab_set()

        tk.Label(v_manual, text="Nombre:", font=("Arial", 9, "bold")).pack(pady=5)
        ent_m_nom = tk.Entry(v_manual); ent_m_nom.pack(fill="x", padx=20)
        ent_m_nom.insert(0, self.ent_nombre.get())

        tk.Label(v_manual, text="Latitud:").pack(); ent_lat = tk.Entry(v_manual); ent_lat.pack(); ent_lat.insert(0, "29.08")
        tk.Label(v_manual, text="Longitud:").pack(); ent_lon = tk.Entry(v_manual); ent_lon.pack(); ent_lon.insert(0, "-110.96")

        def guardar_manual():
            try:
                self.coleccion.add({
                    "nombre": ent_m_nom.get().upper(),
                    "nivel": self.cmb_nivel.get(),
                    "lat": float(ent_lat.get()),
                    "lon": float(ent_lon.get())
                })
                self.cargar_tabla(); v_manual.destroy()
                messagebox.showinfo("Éxito", "Guardado manualmente.")
            except: messagebox.showerror("Error", "Datos inválidos.")

        tk.Button(v_manual, text="CONFIRMAR", bg="#10b981", fg="white", command=guardar_manual).pack(pady=20)

    def cargar_tabla(self):
        for i in self.tabla.get_children(): self.tabla.delete(i)
        if not self.coleccion: return
        try:
            for doc in self.coleccion.stream():
                d = doc.to_dict()
                self.tabla.insert("", "end", values=(d["nombre"], d["nivel"]))
        except: pass

    def guardar_datos(self):
        nom = self.ent_nombre.get().strip()
        niv = self.cmb_nivel.get()
        if not nom or not self.coleccion: return
        try:
            loc = self.geolocator.geocode(f"{nom}, Hermosillo, Sonora")
            if loc:
                self.coleccion.add({"nombre": nom.upper(), "nivel": niv, "lat": loc.latitude, "lon": loc.longitude})
                self.ent_nombre.delete(0, tk.END); self.cargar_tabla()
                messagebox.showinfo("Éxito", "Ubicación guardada.")
            else:
                if messagebox.askyesno("No encontrado", "¿Deseas agregar coordenadas manualmente?"):
                    self.ventana_manual()
        except: pass

    def generar_mapa(self):
        if not self.coleccion: return
        m = folium.Map(location=[29.0892, -110.9613], zoom_start=12)
        for doc in self.coleccion.stream():
            d = doc.to_dict()
            color = self.config_niveles[d["nivel"]]["color"]
            icon_type = "heart" if d["nivel"] == "FUNDACIÓN O ORG" else "graduation-cap"
            folium.Marker([d["lat"], d["lon"]], icon=folium.Icon(color=color, icon=icon_type, prefix="fa")).add_to(m)
            folium.map.Marker([d["lat"], d["lon"]], icon=DivIcon(icon_size=(150,36), html=f'<div style="font-size: 10pt; color: {color}; font-weight: bold; background: white; padding: 2px; border: 1px solid gray; border-radius: 3px; display: inline-block;">{d["nombre"]}</div>')).add_to(m)
        
        archivo = obtener_ruta_real("mapa_nombres.html")
        m.save(archivo); webbrowser.open(f"file:///{archivo}")

    def generar_pdf(self):
        if not self.coleccion: return
        pdf = FPDF()
        pdf.add_page(); pdf.set_font("Arial", "B", 16)
        pdf.cell(190, 10, "REPORTE DE TRAYECTORIA ACADÉMICA Y SOCIAL", ln=True, align="C")
        pdf.ln(10); pdf.set_font("Arial", "B", 12)
        pdf.cell(90, 10, "NOMBRE", 1); pdf.cell(50, 10, "CATEGORÍA", 1); pdf.cell(50, 10, "ESTADO", 1, ln=True)
        pdf.set_font("Arial", "", 10)
        for doc in self.coleccion.stream():
            d = doc.to_dict()
            conf = self.config_niveles[d["nivel"]]
            pdf.cell(90, 10, d["nombre"], 1); pdf.cell(50, 10, d["nivel"], 1)
            pdf.set_fill_color(conf["rgb"][0], conf["rgb"][1], conf["rgb"][2])
            pdf.cell(50, 10, "", 1, ln=True, fill=True)
        
        archivo_pdf = obtener_ruta_real("Reporte_Completo.pdf")
        pdf.output(archivo_pdf); os.startfile(archivo_pdf)

if __name__ == "__main__":
    root = tk.Tk(); app = SistemaMapaHMO(root); root.mainloop()