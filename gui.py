import customtkinter as ctk
import io_manager
from sara import procesar_comando, inicializar

# Configuración de apariencia
ctk.set_appearance_mode("Dark")  # Modes: "System" (standard), "Dark", "Light"
ctk.set_default_color_theme("blue")  # Themes: "blue" (standard), "green", "dark-blue"

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Inicializar SARA
        if not inicializar():
            print("Error al inicializar SARA.")
            return

        # Activar modo GUI en io_manager
        io_manager.activar_modo_gui(None, self.actualizar_interfaz_respuesta, self.show_prompt_gui)

        # Configuración de la ventana
        self.title("SARA")
        self.geometry("400x300")

        # Etiqueta
        self.label = ctk.CTkLabel(self, text="Hola, ¿en qué puedo ayudarte?", font=("Arial", 20))
        self.label.pack(pady=20)

        # Campo de entrada (Entry)
        self.entry = ctk.CTkEntry(self, placeholder_text="Escribe algo...")
        self.entry.pack(pady=10)
        self.entry.bind("<Return>", lambda event: self.button_event())

        # Botón
        self.button = ctk.CTkButton(self, text="Procesar", command=self.button_event)
        self.button.pack(pady=20)

    def button_event(self):
        texto_usuario = self.entry.get().strip()
        if texto_usuario:
            if io_manager.es_comando_salida(texto_usuario):
                io_manager.mostrar_despedida()
                self.quit()
                return
            self.label.configure(text=f"Procesando: {texto_usuario}...")
            # Procesar el comando con SARA
            procesar_comando(texto_usuario)
            # Limpiar el entry después de procesar
            self.entry.delete(0, ctk.END)

    def actualizar_interfaz_respuesta(self, texto_sara):
        # Actualiza la etiqueta con la respuesta de SARA
        self.label.configure(text=f"SARA: {texto_sara}")

    def show_prompt_gui(self, question):
        # Muestra un diálogo de entrada para prompts interactivos
        from customtkinter import CTkInputDialog
        dialog = CTkInputDialog(text=question, title="SARA - Input")
        return dialog.get_input()


if __name__ == "__main__":
    app = App()
    app.mainloop()
