import 'package:flutter_test/flutter_test.dart';

import 'package:flutter_demo/src/app.dart';

void main() {
  testWidgets('renders the demo title', (tester) async {
    await tester.pumpWidget(const DemoApp());
    expect(find.text('Hello from Flutter'), findsOneWidget);
  });
}
