import 'package:flutter/material.dart';

class DemoApp extends StatelessWidget {
  const DemoApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Demo',
      home: Scaffold(
        appBar: AppBar(title: const Text('Demo')),
        body: const Center(child: Text('Hello from Flutter')),
      ),
    );
  }
}
