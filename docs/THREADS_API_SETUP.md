# Configuración de la API de Threads para x_bot_mei

Este documento detalla los pasos necesarios para configurar el acceso a la API de Threads de Meta, lo cual permitirá a `x_bot_mei` publicar y programar mensajes directamente en Threads.

## Pasos a seguir:

1.  **Crear una Cuenta de Desarrollador de Meta:**
    *   Si aún no tienes una, ve a [developers.facebook.com](https://developers.facebook.com) y regístrate o inicia sesión con tu cuenta de Facebook.

2.  **Crear una Nueva Aplicación de Meta:**
    *   Dentro del panel de desarrolladores, haz clic en "Crear aplicación".
    *   Selecciona el tipo de aplicación que mejor se adapte a tu caso (probablemente "Negocio" o "Consumidor").
    *   Sigue los pasos para darle un nombre a tu aplicación y crearla.

3.  **Configurar la API de Threads en tu Aplicación:**
    *   Una vez creada la aplicación, en el panel de control de la aplicación, busca la sección "Productos".
    *   Añade el producto "API de Threads" (o "Instagram Basic Display" si Threads no aparece directamente, ya que la API de Threads a menudo se gestiona a través de la plataforma de Instagram/Meta Graph API).
    *   Sigue las instrucciones para configurar este producto.

4.  **Verificar tu Cuenta de Empresa (si aplica):**
    *   Asegúrate de que la cuenta de Instagram/Threads que deseas usar para publicar esté vinculada a una Página de Facebook y que sea una cuenta de empresa o creador. Es posible que necesites verificar tu negocio en Meta Business Manager.

5.  **Obtener el ID de la Aplicación y el Secreto de la Aplicación:**
    *   En el panel de control de tu aplicación de Meta, ve a "Configuración" -> "Básica".
    *   Aquí encontrarás el "ID de la aplicación" (App ID) y el "Secreto de la aplicación" (App Secret). **Guarda estos valores de forma segura.**

6.  **Configurar un URI de Redirección Válido para OAuth:**
    *   Para obtener tokens de acceso, necesitarás configurar un URI de redirección válido. Esto es crucial para el flujo de autenticación OAuth.
    *   En la configuración de tu aplicación (probablemente bajo "Productos" -> "API de Threads" o "Instagram Basic Display" -> "Configuración"), añade un URI de redirección válido. Para pruebas locales, `https://localhost:8000/` o similar puede funcionar, pero para un entorno de servidor, necesitarás la URL de tu servidor. Por ahora, puedes usar `https://localhost:8000/` como placeholder.

7.  **Generar un Token de Acceso de Usuario:**
    *   Este es el paso más complejo y a menudo requiere un flujo de OAuth. Meta proporciona herramientas en su panel de desarrolladores (como el "Explorador de la API de Graph") para generar tokens de acceso de corta duración. Para tokens de larga duración, el proceso es más elaborado.
    *   **Para empezar, intenta generar un token de acceso de corta duración usando el "Explorador de la API de Graph" de Meta.** Asegúrate de seleccionar los permisos (`scopes`) `threads_basic` y `threads_content_publish`.
    *   **Guarda este token de acceso.** Ten en cuenta que los tokens de corta duración caducan rápidamente. Para una integración robusta, necesitaríamos implementar el flujo completo para obtener tokens de larga duración.

## Datos que necesitaré una vez completados los pasos:

*   **ID de la Aplicación de Meta (App ID)**
*   **Secreto de la Aplicación de Meta (App Secret)**
*   **Token de Acceso de Usuario de Threads (User Access Token)**
*   **ID de la Cuenta de Instagram/Threads (Instagram/Threads Account ID)** (Este a menudo se obtiene a través de la API después de la autenticación inicial, pero si lo obtienes directamente, también es útil).

**Importante:** Nunca compartas el "Secreto de la aplicación" o los tokens de acceso directamente en un canal público. Cuando me los proporciones, asegúrate de hacerlo de forma segura.
