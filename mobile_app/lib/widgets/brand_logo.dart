import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

class BrandLogo extends StatelessWidget {
  final bool compact;
  const BrandLogo({super.key, this.compact = false});

  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Container(width: compact ? 34 : 42, height: compact ? 34 : 42, decoration: BoxDecoration(shape: BoxShape.circle, boxShadow: [BoxShadow(color: AppTheme.violet.withOpacity(.4), blurRadius: 22)]), child: CustomPaint(painter: _HexPainter())),
        if (!compact) ...[
          const SizedBox(width: 12),
          RichText(text: const TextSpan(style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15, letterSpacing: .3), children: [TextSpan(text: 'NEXUS ', style: TextStyle(color: Colors.white)), TextSpan(text: 'AI', style: TextStyle(color: AppTheme.cyan)), TextSpan(text: ' WORKSPACE', style: TextStyle(color: Color(0xffa78bfa), fontSize: 11))])),
        ],
      ]);
}

class _HexPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final paint = Paint()..shader = const LinearGradient(colors: [AppTheme.violet, AppTheme.indigo]).createShader(rect);
    final path = Path()..moveTo(size.width * .5, 0)..lineTo(size.width, size.height * .25)..lineTo(size.width * .86, size.height * .8)..lineTo(size.width * .5, size.height)..lineTo(size.width * .14, size.height * .8)..lineTo(0, size.height * .25)..close();
    canvas.drawPath(path, paint);
    final inner = Paint()..color = AppTheme.obsidian;
    canvas.drawCircle(size.center(Offset.zero), size.width * .22, inner);
    canvas.drawCircle(size.center(Offset.zero), size.width * .1, Paint()..color = AppTheme.cyan);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}