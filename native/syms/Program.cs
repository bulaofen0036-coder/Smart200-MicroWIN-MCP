using System;using System.Runtime.InteropServices;using System.Text;
class P{
 [DllImport("dbghelp.dll",CharSet=CharSet.Ansi,SetLastError=true)]
 static extern int UnDecorateSymbolName(string name, StringBuilder output, int maxLength, int flags);
 static void Main(string[] a){
   foreach(var line in System.IO.File.ReadAllLines(a[0])){
     var sb=new StringBuilder(4096);
     int r=UnDecorateSymbolName(line, sb, 4096, 0x0000);
     Console.WriteLine(r>0? sb.ToString() : line);
   }
 }
}
