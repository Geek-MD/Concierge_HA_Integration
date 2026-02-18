# Concierge Services

Una integración de Home Assistant para gestionar facturas de servicios (electricidad, agua, gas, etc.) recibidas por correo electrónico.

## Características

- **Configuración de correo IMAP**: Configura una cuenta de correo donde recibes tus facturas de servicios
- **Validación de credenciales**: Verifica automáticamente que las credenciales IMAP sean correctas
- **Soporte multiidioma**: Interfaz en español e inglés

## Instalación

### HACS (Recomendado)

1. Asegúrate de tener [HACS](https://hacs.xyz/) instalado
2. Agrega este repositorio como repositorio personalizado en HACS
3. Busca "Concierge Services" en HACS
4. Haz clic en "Instalar"
5. Reinicia Home Assistant

### Manual

1. Copia la carpeta `custom_components/concierge_services` a tu directorio `config/custom_components/`
2. Reinicia Home Assistant

## Configuración

1. Ve a **Configuración** → **Dispositivos y Servicios**
2. Haz clic en el botón **+ Agregar Integración**
3. Busca **Concierge Services**
4. Ingresa los siguientes datos:
   - **Servidor IMAP**: El servidor de correo IMAP (ej: `imap.gmail.com`)
   - **Puerto IMAP**: El puerto IMAP (por defecto: `993`)
   - **Correo Electrónico**: Tu dirección de correo electrónico
   - **Contraseña**: Tu contraseña o contraseña de aplicación

### Ejemplo para Gmail

- **Servidor IMAP**: `imap.gmail.com`
- **Puerto IMAP**: `993`
- **Correo**: `tucorreo@gmail.com`
- **Contraseña**: Usa una [contraseña de aplicación](https://support.google.com/accounts/answer/185833)

### Ejemplo para Outlook/Hotmail

- **Servidor IMAP**: `outlook.office365.com`
- **Puerto IMAP**: `993`
- **Correo**: `tucorreo@outlook.com`
- **Contraseña**: Tu contraseña de cuenta

## Estado del Desarrollo

### ✅ Fase 1: Configuración de Credenciales de Correo (Completada)
- Configuración de cuenta IMAP
- Validación de credenciales
- Interfaz de usuario en español e inglés

### 🚧 Próximas Fases

#### Fase 2: Creación de Sensores
- Configurar sensores individuales por servicio
- Especificar campos del PDF a extraer

#### Fase 3: Lectura de Correos
- Conectar al servidor IMAP
- Filtrar correos de cuentas de servicio
- Descargar archivos PDF adjuntos

#### Fase 4: Extracción de Datos
- Analizar PDFs
- Extraer información (consumo, total a pagar, etc.)

#### Fase 5: Actualización de Sensores
- Actualizar estado del sensor con total a pagar
- Guardar datos adicionales como atributos

## Soporte

Si encuentras algún problema o tienes sugerencias, por favor [abre un issue](https://github.com/Geek-MD/Concierge_Services/issues).

## Licencia

Este proyecto está bajo licencia MIT.

## Créditos

Desarrollado por [@Geek-MD](https://github.com/Geek-MD)