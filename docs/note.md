## 工具清除逻辑
1、当前系统工具信息压缩过程中是否存在重复压缩的问题？压缩过的工具调用信息不应该被二次压缩。

## 会话压缩逻辑
1、当前系统会话的压缩过程中，是否存在对systemprompt的压缩问题？

## State信息替换的逻辑
State 信息替换过程是怎么操作的？手动实现还是使用LangGraph的replace_state方法？
1、手动实现：需要在每个线程中手动调用replace_state方法，将新的state传递给线程。
2、使用LangGraph的replace_state方法：需要在每个线程中手动调用replace_state方法，将新的state传递给线程。
3、其他：其他方法，如使用LangGraph的replace_state方法，需要在每个线程中手动调用replace_state方法，将新的state传递给线程。

## glob grep工具
1、当前系统是否存在glob grep工具？

## 多Agent逻辑
1、当前系统是否存在多Agent逻辑？
2、Isolation：每个Agent之间是否是隔离的？
3、Concurrency：多个Agent是否可以并发执行？
4、Coordination：多个Agent之间是否协调？
5、Monitoring：是否可以监控多个Agent的运行状态？
6、Management：是否可以管理多个Agent的运行？

## 长期记忆
1、长期记忆的逻辑是怎么实现的？
2、长期记忆是使用中间件来管理的吗？