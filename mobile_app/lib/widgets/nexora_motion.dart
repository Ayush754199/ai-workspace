import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

class NexoraMotion extends StatefulWidget {
  final double size;
  final bool compact;
  final bool fullScreen;
  const NexoraMotion({super.key, this.size = 148, this.compact = false, this.fullScreen = false});

  @override
  State<NexoraMotion> createState() => _NexoraMotionState();
}

class _NexoraMotionState extends State<NexoraMotion>
    with SingleTickerProviderStateMixin {
  late final AnimationController controller;

  @override
  void initState() {
    super.initState();
    controller = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 5),
    )..repeat();
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: controller,
        builder: (context, child) => SizedBox(
          width: widget.fullScreen ? double.infinity : widget.size,
          height: widget.fullScreen ? double.infinity : widget.size,
          child: CustomPaint(
            painter: _NexoraPainter(controller.value, widget.compact),
          ),
        ),
      );
}

class _NexoraPainter extends CustomPainter {
  final double progress;
  final bool compact;
  _NexoraPainter(this.progress, this.compact);

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.shortestSide * .31;
    final pulse = 1 + math.sin(progress * math.pi * 2) * .07;
    final glow = Paint()
      ..shader = RadialGradient(colors: [
        AppTheme.cyan.withOpacity(.55),
        AppTheme.violet.withOpacity(.18),
        Colors.transparent,
      ]).createShader(Rect.fromCircle(center: center, radius: radius * 2.3));
    canvas.drawCircle(center, radius * 2.25, glow);

    for (var i = 0; i < 3; i++) {
      final angle = progress * math.pi * 2 * (i.isEven ? 1 : -1) + i * 2.1;
      final orbit = radius * (1.35 + i * .22);
      final dot = Offset(center.dx + math.cos(angle) * orbit,
          center.dy + math.sin(angle) * orbit);
      canvas.drawCircle(dot, size.shortestSide * (compact ? .025 : .035),
          Paint()..color = i == 1 ? AppTheme.cyan : AppTheme.violet);
    }

    final orb = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [AppTheme.cyan, AppTheme.indigo, AppTheme.violet],
      ).createShader(Rect.fromCircle(center: center, radius: radius));
    canvas.drawCircle(center, radius * pulse, orb);
    canvas.drawCircle(center, radius * .68, Paint()..color = AppTheme.obsidian);
    final core = Paint()
      ..shader = RadialGradient(colors: [
        Colors.white.withOpacity(.95),
        AppTheme.cyan.withOpacity(.7),
        Colors.transparent,
      ]).createShader(Rect.fromCircle(center: center, radius: radius * .45));
    canvas.drawCircle(center, radius * .45, core);
  }

  @override
  bool shouldRepaint(covariant _NexoraPainter oldDelegate) =>
      oldDelegate.progress != progress || oldDelegate.compact != compact;
}

class NexoraTyping extends StatefulWidget {
  final String label;
  const NexoraTyping({super.key, this.label = 'Nexora is thinking'});

  @override
  State<NexoraTyping> createState() => _NexoraTypingState();
}

class _NexoraTypingState extends State<NexoraTyping>
    with SingleTickerProviderStateMixin {
  late final AnimationController controller;

  @override
  void initState() {
    super.initState();
    controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1100),
    )..repeat();
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          AnimatedBuilder(
            animation: controller,
            builder: (_, __) => Row(
              children: List.generate(3, (index) {
                final value =
                    math.sin((controller.value + index / 3) * math.pi * 2);
                return Padding(
                  padding: const EdgeInsets.only(right: 4),
                  child: Transform.translate(
                    offset: Offset(0, value * 3),
                    child: const CircleAvatar(
                        radius: 3, backgroundColor: AppTheme.cyan),
                  ),
                );
              }),
            ),
          ),
          const SizedBox(width: 4),
          Text(widget.label, style: Theme.of(context).textTheme.bodySmall),
        ],
      );
}
