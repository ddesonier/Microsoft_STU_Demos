# Loading Local Docker Images

This guide explains how to load and use local Docker images for development and testing purposes.

---

## Prerequisites

- Docker installed on your system.
- A local Docker image file (`.tar` or `.tar.gz`) exported using `docker save`.

---

## Steps to Load a Local Docker Image

### 1️⃣ Export the Docker Image (Optional)
If you need to export a Docker image from another system, use:
```bash
docker save -o <image-name>.tar <image-name>:<tag>
```
This creates a `.tar` file containing the Docker image.

---

### 2️⃣ Transfer the Image File
Move the `.tar` file to the system where you want to load the image.

---

### 3️⃣ Load the Docker Image
Use the `docker load` command to import the image:
```bash
docker load -i <image-name>.tar
```
This will load the image into your local Docker environment.

---

### 4️⃣ Verify the Image
Check if the image was loaded successfully:
```bash
docker images
```
You should see the image listed with its name and tag.

---

### 5️⃣ Run the Docker Container
Start a container using the loaded image:
```bash
docker run -p 8000:8000 <image-name>:<tag>
```
Replace `<image-name>` and `<tag>` with the name and tag of your image.

---

## Example Workflow

1. Export the image:
   ```bash
   docker save -o code-assistant.tar code-assistant:latest
   ```

2. Transfer the `code-assistant.tar` file to the target system.

3. Load the image:
   ```bash
   docker load -i code-assistant.tar
   ```

4. Verify the image:
   ```bash
   docker images
   ```

5. Run the container:
   ```bash
   docker run -p 8000:8000 code-assistant:latest
   ```

---

## Notes

- Ensure the image file is not corrupted during transfer.
- Use `docker save` and `docker load` for portability between systems.
- If you encounter issues, check Docker logs using:
  ```bash
  docker logs <container-id>
  ```

---

This guide helps you load and use local Docker images efficiently for development and