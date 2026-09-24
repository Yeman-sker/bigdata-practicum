// GPU layer for inventory particles: one instanced quad per bike.
// The shader reproduces the canvas path exactly: same orbit formula, the same
// half-pixel radius buckets and the same four-stop radial glow, added with
// "lighter"-style additive blending.

/** Floats per particle: center(2), east axis(2), north axis(2), radius, angle, speed. */
export const PARTICLE_STRIDE = 9;

const vertexSource = `#version 300 es
precision highp float;
layout(location = 0) in vec2 corner;
layout(location = 1) in vec2 center;
layout(location = 2) in vec2 east;
layout(location = 3) in vec2 north;
layout(location = 4) in vec3 orbit; // radius, angle, speed
uniform vec2 viewport;
uniform float time;
uniform float size;
out vec2 local;
out float alpha;
void main() {
  float angle = orbit.y + time * orbit.z;
  float s = sin(angle);
  float near = 0.5 - s * 0.5;
  vec2 position = center + orbit.x * cos(angle) * east + orbit.x * s * north;
  float radius = max(1.0, floor((4.2 + near * 1.6) * size * 2.0 + 0.5) / 2.0);
  alpha = min(1.0, 0.62 + near * 0.38);
  local = corner;
  vec2 pixel = position + corner * radius;
  gl_Position = vec4(pixel / viewport * 2.0 - 1.0, 0.0, 1.0) * vec4(1.0, -1.0, 1.0, 1.0);
}`;

const fragmentSource = `#version 300 es
precision highp float;
in vec2 local;
in float alpha;
uniform vec3 tint;
out vec4 color;
void main() {
  float t = length(local);
  // Canvas gradient stops: 0 white/1, 0.14 tint/1, 0.38 tint/0.32, 1 tint/0,
  // interpolated unpremultiplied, then premultiplied for additive output.
  vec4 c;
  if (t < 0.14) c = mix(vec4(1.0), vec4(tint, 1.0), t / 0.14);
  else if (t < 0.38) c = mix(vec4(tint, 1.0), vec4(tint, 0.32), (t - 0.14) / 0.24);
  else if (t < 1.0) c = mix(vec4(tint, 0.32), vec4(tint, 0.0), (t - 0.38) / 0.62);
  else c = vec4(tint, 0.0);
  float a = c.a * alpha;
  color = vec4(c.rgb * a, a);
}`;

export type BikeLayer = {
  resize(width: number, height: number, ratio: number): void;
  setParticles(data: Float32Array, count: number): void;
  draw(time: number, size: number): void;
  dispose(): void;
};

function compile(gl: WebGL2RenderingContext, type: number, source: string) {
  const shader = gl.createShader(type)!;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
    throw new Error(gl.getShaderInfoLog(shader) ?? "shader compile failed");
  return shader;
}

/** Returns null when WebGL2 is unavailable; callers then keep the canvas path. */
export function createBikeLayer(
  canvas: HTMLCanvasElement,
  tint: [number, number, number],
): BikeLayer | null {
  const gl = canvas.getContext("webgl2", {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    preserveDrawingBuffer: false,
  });
  if (!gl || gl.isContextLost()) return null;
  let program: WebGLProgram;
  try {
    program = gl.createProgram()!;
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertexSource));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragmentSource));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  } catch {
    return null;
  }
  const uniforms = {
    viewport: gl.getUniformLocation(program, "viewport"),
    time: gl.getUniformLocation(program, "time"),
    size: gl.getUniformLocation(program, "size"),
    tint: gl.getUniformLocation(program, "tint"),
  };
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  const instances = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, instances);
  const bytes = PARTICLE_STRIDE * 4;
  const layout: [number, number, number][] = [
    [1, 2, 0],
    [2, 2, 2],
    [3, 2, 4],
    [4, 3, 6],
  ];
  for (const [location, components, offset] of layout) {
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, components, gl.FLOAT, false, bytes, offset * 4);
    gl.vertexAttribDivisor(location, 1);
  }
  gl.bindVertexArray(null);
  let count = 0;
  let width = 1,
    height = 1;
  let capacity = 0;

  return {
    resize(w, h, ratio) {
      width = w;
      height = h;
      canvas.width = Math.round(w * ratio);
      canvas.height = Math.round(h * ratio);
      gl.viewport(0, 0, canvas.width, canvas.height);
    },
    setParticles(data, n) {
      count = n;
      gl.bindBuffer(gl.ARRAY_BUFFER, instances);
      const used = n * PARTICLE_STRIDE;
      if (used > capacity) {
        capacity = Math.max(used, capacity * 2);
        gl.bufferData(gl.ARRAY_BUFFER, capacity * 4, gl.DYNAMIC_DRAW);
      }
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, data, 0, used);
    },
    draw(time, size) {
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      if (!count) return;
      gl.useProgram(program);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.uniform2f(uniforms.viewport, width, height);
      gl.uniform1f(uniforms.time, time);
      gl.uniform1f(uniforms.size, size);
      gl.uniform3f(uniforms.tint, tint[0], tint[1], tint[2]);
      gl.bindVertexArray(vao);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, count);
      gl.bindVertexArray(null);
    },
    // Release GPU objects but keep the context: the canvas element can be
    // reused by a remount (React StrictMode), and a lost context cannot.
    dispose() {
      gl.deleteBuffer(quad);
      gl.deleteBuffer(instances);
      gl.deleteVertexArray(vao);
      gl.deleteProgram(program);
    },
  };
}
