/**
 * Canvas-based Real-time 60fps Audio Waveform Visualizer
 * Animates organic acoustic sound oscillations during phone calls and AI speech playback.
 */
class AudioWaveformVisualizer {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
    this.isActive = false;
    this.animationId = null;
    this.phase = 0;
    this.amplitude = 0.2;
    this.targetAmplitude = 0.2;
    this.bars = 48;

    if (this.canvas) {
      this.resize();
      window.addEventListener('resize', () => this.resize());
    }
  }

  resize() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = rect.width * (window.devicePixelRatio || 1);
    this.canvas.height = rect.height * (window.devicePixelRatio || 1);
    if (this.ctx) {
      this.ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
    }
  }

  start(isSpeaking = true) {
    this.isActive = true;
    this.targetAmplitude = isSpeaking ? 0.85 : 0.35;
    if (!this.animationId) {
      this.animate();
    }
  }

  setSpeaking(speaking) {
    this.targetAmplitude = speaking ? 0.95 : 0.25;
  }

  stop() {
    this.isActive = false;
    this.targetAmplitude = 0.05;
  }

  animate() {
    if (!this.canvas || !this.ctx) return;
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;

    this.ctx.clearRect(0, 0, width, height);

    // Smooth amplitude transition
    this.amplitude += (this.targetAmplitude - this.amplitude) * 0.12;
    this.phase += 0.08;

    const centerY = height / 2;
    const barWidth = Math.max(3, (width / this.bars) - 3);

    for (let i = 0; i < this.bars; i++) {
      const x = i * (barWidth + 3) + 4;
      const progress = i / this.bars;
      
      // Calculate dynamic bar height using multi-frequency sine wave
      const freq1 = Math.sin(progress * Math.PI * 3 + this.phase);
      const freq2 = Math.cos(progress * Math.PI * 5 - this.phase * 0.7);
      const barHeight = Math.max(4, Math.abs(freq1 * 0.6 + freq2 * 0.4) * (centerY - 8) * this.amplitude);

      // Gradient color (Warm Bronze to Deep Espresso)
      const grad = this.ctx.createLinearGradient(0, centerY - barHeight, 0, centerY + barHeight);
      grad.addColorStop(0, '#CBB59D');
      grad.addColorStop(0.5, '#8C5E3C');
      grad.addColorStop(1, '#2B2118');

      this.ctx.fillStyle = grad;
      this.ctx.beginPath();
      if (this.ctx.roundRect) {
        this.ctx.roundRect(x, centerY - barHeight, barWidth, barHeight * 2, 3);
      } else {
        this.ctx.rect(x, centerY - barHeight, barWidth, barHeight * 2);
      }
      this.ctx.fill();
    }

    if (this.isActive || this.amplitude > 0.06) {
      this.animationId = requestAnimationFrame(() => this.animate());
    } else {
      this.animationId = null;
      this.ctx.clearRect(0, 0, width, height);
    }
  }
}

window.AudioWaveformVisualizer = AudioWaveformVisualizer;
