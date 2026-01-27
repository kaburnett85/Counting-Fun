// Game State
let targetNumber = 0;
let currentCount = 0;
const TOTAL_DOTS = 10;

// DOM Elements
const targetNumberDisplay = document.getElementById('target-number');
const sourceArea = document.getElementById('source-area');
const targetArea = document.getElementById('target-area');
const dotCountDisplay = document.getElementById('dot-count');
const submitBtn = document.getElementById('submit-btn');
const newGameBtn = document.getElementById('new-game-btn');
const feedback = document.getElementById('feedback');
const fireworksCanvas = document.getElementById('fireworks-canvas');
const ctx = fireworksCanvas.getContext('2d');

// Fireworks state
let fireworks = [];
let particles = [];
let fireworksActive = false;

// Initialize the game
function initGame() {
    // Generate random number between 1 and 10
    targetNumber = Math.floor(Math.random() * 10) + 1;
    targetNumberDisplay.textContent = targetNumber;

    // Reset count
    currentCount = 0;
    dotCountDisplay.textContent = currentCount;

    // Clear areas
    sourceArea.innerHTML = '';
    targetArea.innerHTML = '';

    // Hide feedback
    feedback.classList.remove('show', 'correct', 'incorrect');
    feedback.textContent = '';

    // Stop any active fireworks
    fireworksActive = false;

    // Create dots in source area
    createDots();
}

// Create colorful dots
function createDots() {
    for (let i = 0; i < TOTAL_DOTS; i++) {
        const dot = document.createElement('div');
        dot.className = `dot color-${(i % 10) + 1}`;
        dot.draggable = true;
        dot.dataset.id = i;

        // Mouse drag events
        dot.addEventListener('dragstart', handleDragStart);
        dot.addEventListener('dragend', handleDragEnd);

        // Touch events for mobile
        dot.addEventListener('touchstart', handleTouchStart, { passive: false });
        dot.addEventListener('touchmove', handleTouchMove, { passive: false });
        dot.addEventListener('touchend', handleTouchEnd);

        sourceArea.appendChild(dot);
    }
}

// Drag and Drop Handlers
let draggedDot = null;

function handleDragStart(e) {
    draggedDot = e.target;
    e.target.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', e.target.dataset.id);
}

function handleDragEnd(e) {
    e.target.classList.remove('dragging');
    draggedDot = null;
}

// Drop zone handlers
targetArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    targetArea.classList.add('drag-over');
});

targetArea.addEventListener('dragleave', () => {
    targetArea.classList.remove('drag-over');
});

targetArea.addEventListener('drop', (e) => {
    e.preventDefault();
    targetArea.classList.remove('drag-over');

    if (draggedDot && draggedDot.parentElement !== targetArea) {
        targetArea.appendChild(draggedDot);
        updateCount();
        playClickSound();
    }
});

// Allow dragging back to source
sourceArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
});

sourceArea.addEventListener('drop', (e) => {
    e.preventDefault();

    if (draggedDot && draggedDot.parentElement !== sourceArea) {
        sourceArea.appendChild(draggedDot);
        updateCount();
        playClickSound();
    }
});

// Touch handlers for mobile support
let touchDot = null;
let touchClone = null;
let touchStartArea = null;

function handleTouchStart(e) {
    e.preventDefault();
    touchDot = e.target;
    touchStartArea = touchDot.parentElement;
    touchDot.classList.add('dragging');

    // Create a visual clone for dragging
    touchClone = touchDot.cloneNode(true);
    touchClone.style.position = 'fixed';
    touchClone.style.pointerEvents = 'none';
    touchClone.style.zIndex = '10000';
    touchClone.style.opacity = '0.8';
    document.body.appendChild(touchClone);

    updateTouchClonePosition(e.touches[0]);
}

function handleTouchMove(e) {
    e.preventDefault();
    if (touchClone) {
        updateTouchClonePosition(e.touches[0]);
    }

    // Check if over target area
    const touch = e.touches[0];
    const targetRect = targetArea.getBoundingClientRect();
    const sourceRect = sourceArea.getBoundingClientRect();

    if (isPointInRect(touch, targetRect)) {
        targetArea.classList.add('drag-over');
        sourceArea.classList.remove('drag-over');
    } else if (isPointInRect(touch, sourceRect)) {
        sourceArea.classList.add('drag-over');
        targetArea.classList.remove('drag-over');
    } else {
        targetArea.classList.remove('drag-over');
        sourceArea.classList.remove('drag-over');
    }
}

function handleTouchEnd(e) {
    if (!touchDot) return;

    touchDot.classList.remove('dragging');

    // Remove clone
    if (touchClone) {
        touchClone.remove();
        touchClone = null;
    }

    // Determine drop target
    const touch = e.changedTouches[0];
    const targetRect = targetArea.getBoundingClientRect();
    const sourceRect = sourceArea.getBoundingClientRect();

    if (isPointInRect(touch, targetRect) && touchStartArea !== targetArea) {
        targetArea.appendChild(touchDot);
        updateCount();
        playClickSound();
    } else if (isPointInRect(touch, sourceRect) && touchStartArea !== sourceArea) {
        sourceArea.appendChild(touchDot);
        updateCount();
        playClickSound();
    }

    targetArea.classList.remove('drag-over');
    sourceArea.classList.remove('drag-over');
    touchDot = null;
    touchStartArea = null;
}

function updateTouchClonePosition(touch) {
    if (touchClone) {
        touchClone.style.left = (touch.clientX - 25) + 'px';
        touchClone.style.top = (touch.clientY - 25) + 'px';
    }
}

function isPointInRect(point, rect) {
    return point.clientX >= rect.left &&
           point.clientX <= rect.right &&
           point.clientY >= rect.top &&
           point.clientY <= rect.bottom;
}

// Update dot count
function updateCount() {
    currentCount = targetArea.children.length;
    dotCountDisplay.textContent = currentCount;
}

// Simple click sound using Web Audio API
function playClickSound() {
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = audioCtx.createOscillator();
        const gainNode = audioCtx.createGain();

        oscillator.connect(gainNode);
        gainNode.connect(audioCtx.destination);

        oscillator.frequency.value = 800;
        oscillator.type = 'sine';
        gainNode.gain.value = 0.1;

        oscillator.start();
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.1);
        oscillator.stop(audioCtx.currentTime + 0.1);
    } catch (e) {
        // Audio not supported, continue silently
    }
}

// Check answer
submitBtn.addEventListener('click', checkAnswer);

function checkAnswer() {
    if (currentCount === targetNumber) {
        // Correct!
        feedback.textContent = '🎉 Good Job! 🎉';
        feedback.className = 'feedback show correct';
        speak('Good job!', 1.0, 1.2);
        startFireworks();
    } else {
        // Incorrect
        feedback.textContent = '💭 Try Again! 💭';
        feedback.className = 'feedback show incorrect';
        speak('Try again', 0.8, 0.9);
    }
}

// Text-to-speech for audio feedback
function speak(text, rate = 1.0, pitch = 1.0) {
    if ('speechSynthesis' in window) {
        // Cancel any ongoing speech
        window.speechSynthesis.cancel();

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = rate;
        utterance.pitch = pitch;
        utterance.volume = 1.0;

        // Try to find a friendly voice
        const voices = window.speechSynthesis.getVoices();
        const friendlyVoice = voices.find(voice =>
            voice.name.includes('Female') ||
            voice.name.includes('Samantha') ||
            voice.name.includes('Victoria') ||
            voice.lang.startsWith('en')
        );

        if (friendlyVoice) {
            utterance.voice = friendlyVoice;
        }

        window.speechSynthesis.speak(utterance);
    }
}

// Load voices when they become available
if ('speechSynthesis' in window) {
    window.speechSynthesis.onvoiceschanged = () => {
        window.speechSynthesis.getVoices();
    };
}

// New game button
newGameBtn.addEventListener('click', initGame);

// ============ FIREWORKS ANIMATION ============

function resizeCanvas() {
    fireworksCanvas.width = window.innerWidth;
    fireworksCanvas.height = window.innerHeight;
}

window.addEventListener('resize', resizeCanvas);
resizeCanvas();

class Firework {
    constructor() {
        this.x = Math.random() * fireworksCanvas.width;
        this.y = fireworksCanvas.height;
        this.targetY = Math.random() * (fireworksCanvas.height * 0.5) + 50;
        this.speed = 8 + Math.random() * 4;
        this.angle = -Math.PI / 2 + (Math.random() - 0.5) * 0.3;
        this.vx = Math.cos(this.angle) * this.speed;
        this.vy = Math.sin(this.angle) * this.speed;
        this.trail = [];
        this.exploded = false;
        this.color = `hsl(${Math.random() * 360}, 100%, 60%)`;
    }

    update() {
        if (!this.exploded) {
            this.trail.push({ x: this.x, y: this.y });
            if (this.trail.length > 10) {
                this.trail.shift();
            }

            this.x += this.vx;
            this.y += this.vy;
            this.vy += 0.1; // gravity

            if (this.y <= this.targetY || this.vy >= 0) {
                this.explode();
            }
        }
    }

    explode() {
        this.exploded = true;
        const particleCount = 60 + Math.floor(Math.random() * 40);
        const hue = Math.random() * 360;

        for (let i = 0; i < particleCount; i++) {
            const angle = (Math.PI * 2 * i) / particleCount + (Math.random() - 0.5) * 0.5;
            const speed = 2 + Math.random() * 4;
            particles.push(new Particle(
                this.x,
                this.y,
                Math.cos(angle) * speed,
                Math.sin(angle) * speed,
                `hsl(${hue + Math.random() * 30}, 100%, ${50 + Math.random() * 30}%)`
            ));
        }
    }

    draw() {
        if (!this.exploded) {
            // Draw trail
            for (let i = 0; i < this.trail.length; i++) {
                const alpha = i / this.trail.length;
                ctx.beginPath();
                ctx.arc(this.trail[i].x, this.trail[i].y, 2, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(255, 200, 100, ${alpha * 0.5})`;
                ctx.fill();
            }

            // Draw firework head
            ctx.beginPath();
            ctx.arc(this.x, this.y, 4, 0, Math.PI * 2);
            ctx.fillStyle = this.color;
            ctx.fill();
        }
    }
}

class Particle {
    constructor(x, y, vx, vy, color) {
        this.x = x;
        this.y = y;
        this.vx = vx;
        this.vy = vy;
        this.color = color;
        this.alpha = 1;
        this.decay = 0.015 + Math.random() * 0.01;
        this.size = 2 + Math.random() * 2;
    }

    update() {
        this.x += this.vx;
        this.y += this.vy;
        this.vy += 0.05; // gravity
        this.vx *= 0.99; // air resistance
        this.alpha -= this.decay;
    }

    draw() {
        ctx.save();
        ctx.globalAlpha = Math.max(0, this.alpha);
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.fillStyle = this.color;
        ctx.fill();
        ctx.restore();
    }
}

function animateFireworks() {
    if (!fireworksActive && fireworks.length === 0 && particles.length === 0) {
        ctx.clearRect(0, 0, fireworksCanvas.width, fireworksCanvas.height);
        return;
    }

    ctx.fillStyle = 'rgba(0, 0, 0, 0.1)';
    ctx.fillRect(0, 0, fireworksCanvas.width, fireworksCanvas.height);

    // Update and draw fireworks
    for (let i = fireworks.length - 1; i >= 0; i--) {
        fireworks[i].update();
        fireworks[i].draw();
        if (fireworks[i].exploded) {
            fireworks.splice(i, 1);
        }
    }

    // Update and draw particles
    for (let i = particles.length - 1; i >= 0; i--) {
        particles[i].update();
        particles[i].draw();
        if (particles[i].alpha <= 0) {
            particles.splice(i, 1);
        }
    }

    requestAnimationFrame(animateFireworks);
}

function startFireworks() {
    fireworksActive = true;
    ctx.clearRect(0, 0, fireworksCanvas.width, fireworksCanvas.height);

    // Launch initial burst
    for (let i = 0; i < 5; i++) {
        setTimeout(() => {
            if (fireworksActive) {
                fireworks.push(new Firework());
            }
        }, i * 200);
    }

    // Continue launching for 3 seconds
    let launchCount = 0;
    const launchInterval = setInterval(() => {
        if (fireworksActive && launchCount < 10) {
            fireworks.push(new Firework());
            launchCount++;
        } else {
            clearInterval(launchInterval);
        }
    }, 300);

    // Stop after 4 seconds
    setTimeout(() => {
        fireworksActive = false;
    }, 4000);

    animateFireworks();
}

// Initialize game on load
window.addEventListener('load', () => {
    initGame();

    // Pre-load speech synthesis voices
    if ('speechSynthesis' in window) {
        window.speechSynthesis.getVoices();
    }
});
