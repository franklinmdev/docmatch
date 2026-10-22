// Drives DGII's pre-validator form in-process: fills the fields, sets the
// stream the file dialog would set, presses Verificar, waits for the worker
// thread and prints what the form shows. Usage:
//   mono Harness.exe <app dir> <606 file> <rnc> <periodo> [itbis] [monto]
using System;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

static class Harness
{
    const BindingFlags Any = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    static object Get(object o, string name)
    {
        var p = o.GetType().GetProperty(name, Any);
        if (p != null) return p.GetValue(o, null);
        return o.GetType().GetField(name, Any).GetValue(o);
    }

    static void Set(object o, string field, object value)
    {
        o.GetType().GetField(field, Any).SetValue(o, value);
    }

    [STAThread]
    static int Main(string[] a)
    {
        var asm = Assembly.LoadFrom(Path.Combine(a[0], "DGII.PreValidado.exe"));
        var form = (Form)Activator.CreateInstance(asm.GetType("DGII.PreValidado.frm_Verified"));
        var timer = new System.Windows.Forms.Timer { Interval = 500 };
        bool started = false;
        int ticks = 0;
        timer.Tick += (s, e) =>
        {
            ticks++;
            if (!started)
            {
                started = true;
                var combo = (ComboBox)Get(form, "cbFormato");
                for (int i = 0; i < combo.Items.Count; i++)
                    if (combo.Items[i].ToString().StartsWith("606")) combo.SelectedIndex = i;
                ((TextBox)Get(form, "txtRnc")).Text = a[2];
                ((TextBox)Get(form, "txtPeriodo")).Text = a[3];
                var itbis = (TextBox)Get(form, "txtITBIFac");
                var monto = (TextBox)Get(form, "txtMontoFac");
                Console.WriteLine("totals boxes visible: itbis=" + itbis.Visible + " monto=" + monto.Visible);
                if (a.Length > 4) itbis.Text = a[4];
                if (a.Length > 5) monto.Text = a[5];
                Set(form, "strm", File.OpenRead(a[1]));
                ((TextBox)Get(form, "txtpath")).Text = a[1];
                ((Button)Get(form, "btnVerified")).PerformClick();
                return;
            }
            var status = ((Label)Get(form, "lblDetalleBarra")).Text;
            if (status != "Proceso Completado" && ticks < 240) return;
            timer.Stop();
            Console.WriteLine("finished: " + (status == "Proceso Completado") + " after ticks " + ticks);
            Console.WriteLine("lblDetalleBarra: " + ((Label)Get(form, "lblDetalleBarra")).Text);
            Console.WriteLine("lblErrores: " + ((Label)Get(form, "lblErrores")).Text);
            Console.WriteLine("lblTotalR: " + ((Label)Get(form, "lblTotalR")).Text);
            Console.WriteLine("btnGuardar visible: " + ((Button)Get(form, "btnGuardar")).Visible);
            Console.WriteLine("btnVerErr visible: " + ((Button)Get(form, "btnVerErr")).Visible);
            Console.WriteLine("Count: " + Get(form, "Count"));
            Console.WriteLine("strRespuesta: " + Get(form, "strRespuesta"));
            Console.WriteLine("txtError:\n" + ((TextBox)Get(form, "txtError")).Text);
            var verErr = (Button)Get(form, "btnVerErr");
            if (verErr.Visible)
            {
                try { verErr.PerformClick(); }
                catch (Exception ex) { Console.WriteLine("Ver Errores threw: " + ex.GetType().Name + ": " + ex.Message); }
            }
            Console.Out.Flush();
            File.WriteAllText("/out/ready", "1");
        };
        form.Shown += (s, e) => timer.Start();
        Application.Run(form);
        return 0;
    }
}
