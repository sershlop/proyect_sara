import tkinter as tk

# Crear la ventana principal
root = tk.Tk()
root.title("Calculadora Básica")
root.geometry("300x150")

# Crear la pantalla
display = tk.Entry(root, width=20, font=('Arial', 16))
display.pack(pady=10)

# Crear los botones
buttons = [
    "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "+", "−", "×", "÷", "C", "⌫"
]

# Crear los botones y añadirlos al interface
for i in range(10):
    btn = tk.Button(root, text=buttons[i], font=('Arial', 16), width=10)
    if i < 9:
        btn.pack(side=tk.LEFT, padx=5)
    else:
        btn.pack(side=tk.TOP, padx=5)

# Función para calcular
def calculate():
    try:
        total = eval(display.get())
        display.delete(0, tk.END)
        display.insert(tk.END, str(total))
    except Exception as e:
        print(f"Error: {str(e)}")

# Función para borrar
def clear():
    display.delete(0, tk.END)

# Asignar eventos
display.bind("<Return>", calculate)
display.bind("<Delete>", clear)

# Iniciar el bucle principal
root.mainloop()
